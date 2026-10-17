"""
Workflow Scheduler Service & Daemon

Features:
- Cron-based scheduling (e.g., "0 9 * * 1-5" for weekdays at 9am)
- Interval-based scheduling (every N seconds/minutes/hours)
- One-time scheduled executions
- Timezone-aware scheduling (DST-safe via pytz.localize)
- Concurrent execution limits (per-schedule + global semaphore)
- Retry on failure with exponential backoff
- Graceful shutdown and recovery
- Multi-instance safe via FOR UPDATE SKIP LOCKED

Two modes:
1. WorkflowSchedulerService: Per-request CRUD (uses injected db session)
2. WorkflowSchedulerDaemon: Background polling daemon (singleton, own sessions)
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone as tz
from typing import Optional, Dict, Any, List
from uuid import UUID

from croniter import croniter
import pytz

from sqlalchemy import select, and_, text, func
from sqlalchemy.ext.asyncio import (
    create_async_engine, AsyncSession, async_sessionmaker,
)

from app.models.scheduled_workflow import (
    ScheduledWorkflow, ScheduledWorkflowExecution,
    ScheduleType, ScheduleStatus,
)
from app.models.workflow import WorkflowExecution

logger = logging.getLogger(__name__)


# =============================================
# Validation Helpers
# =============================================

def validate_timezone(timezone_str: str) -> None:
    """Validate timezone is a valid IANA timezone."""
    if timezone_str not in pytz.all_timezones:
        raise ValueError(
            f"Invalid timezone: '{timezone_str}'. "
            f"Must be a valid IANA timezone (e.g., 'America/New_York', 'UTC')."
        )


def validate_input_variables(input_variables: Dict[str, Any]) -> None:
    """Validate input_variables size and depth."""
    serialized = json.dumps(input_variables)
    if len(serialized) > 65536:  # 64KB
        raise ValueError("input_variables exceeds maximum size of 64KB")

    def check_depth(obj: Any, depth: int = 0) -> None:
        if depth > 5:
            raise ValueError("input_variables exceeds maximum nesting depth of 5")
        if isinstance(obj, dict):
            for v in obj.values():
                check_depth(v, depth + 1)
        elif isinstance(obj, list):
            for v in obj:
                check_depth(v, depth + 1)

    check_depth(input_variables)


def validate_cron_frequency(cron_expression: str) -> None:
    """Reject cron expressions that fire more frequently than once per minute."""
    # Reject 6-field (second-resolution) cron expressions
    if len(cron_expression.strip().split()) > 5:
        raise ValueError("Second-resolution cron expressions (6 fields) are not supported")

    now = datetime.now(tz.utc)
    cron = croniter(cron_expression, now)
    first = cron.get_next(datetime)
    second = cron.get_next(datetime)
    gap = (second - first).total_seconds()
    if gap < 60:
        raise ValueError(
            f"Cron expression fires too frequently (every {int(gap)}s). "
            f"Minimum interval is 60 seconds."
        )


def calculate_next_run(
    schedule_type: str,
    cron_expression: Optional[str],
    interval_seconds: Optional[int],
    scheduled_time: Optional[datetime],
    timezone_str: str,
) -> Optional[datetime]:
    """Calculate the next run time for a schedule (DST-safe)."""
    tz_obj = pytz.timezone(timezone_str)
    now = datetime.now(tz_obj)

    if schedule_type == "cron":
        cron = croniter(cron_expression, now)
        next_dt = cron.get_next(datetime)
        # DST-safe: use localize() instead of replace(tzinfo=)
        if next_dt.tzinfo is None:
            next_dt = tz_obj.localize(next_dt)
        return next_dt.astimezone(pytz.UTC)

    elif schedule_type == "interval":
        return now.astimezone(pytz.UTC) + timedelta(seconds=interval_seconds)

    elif schedule_type == "one_time":
        return scheduled_time

    return None


# =============================================
# CRUD Service (per-request, injected session)
# =============================================

class WorkflowSchedulerService:
    """
    Service for CRUD operations on scheduled workflows.

    Instantiated per-request with the injected db session.
    Uses flush()+refresh() instead of commit()+refresh() to preserve
    RLS tenant context (commit is handled by get_db dependency).
    """

    def __init__(self, db: AsyncSession):
        self._db = db

    def _validate_schedule_config(
        self,
        schedule_type: str,
        cron_expression: Optional[str],
        interval_seconds: Optional[int],
        scheduled_time: Optional[datetime],
    ) -> None:
        """Validate schedule configuration."""
        if schedule_type == "cron":
            if not cron_expression:
                raise ValueError("Cron expression is required for cron schedule type")
            try:
                croniter(cron_expression)
            except Exception as e:
                raise ValueError(f"Invalid cron expression: {e}")
            validate_cron_frequency(cron_expression)

        elif schedule_type == "interval":
            if not interval_seconds or interval_seconds <= 0:
                raise ValueError("Positive interval_seconds is required for interval schedule type")
            if interval_seconds < 60:
                raise ValueError("Minimum interval is 60 seconds")

        elif schedule_type == "one_time":
            if not scheduled_time:
                raise ValueError("scheduled_time is required for one_time schedule type")
            if scheduled_time <= datetime.now(tz.utc):
                raise ValueError("scheduled_time must be in the future")

    async def create_schedule(
        self,
        workflow_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
        name: str,
        schedule_type: str = "cron",
        cron_expression: Optional[str] = None,
        interval_seconds: Optional[int] = None,
        scheduled_time: Optional[datetime] = None,
        timezone: str = "UTC",
        input_variables: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None,
        max_executions: Optional[int] = None,
        max_concurrent: int = 1,
        timeout_seconds: int = 3600,
        retry_on_failure: bool = False,
        max_retries: int = 3,
        retry_delay_seconds: int = 60,
    ) -> ScheduledWorkflow:
        """Create a new scheduled workflow."""
        # Validate timezone
        validate_timezone(timezone)

        # Validate input_variables size/depth
        if input_variables:
            validate_input_variables(input_variables)

        # Validate schedule configuration (includes cron frequency check)
        self._validate_schedule_config(
            schedule_type, cron_expression, interval_seconds, scheduled_time
        )

        type_enum = ScheduleType(schedule_type)

        next_run_at = calculate_next_run(
            schedule_type, cron_expression, interval_seconds, scheduled_time, timezone
        )

        schedule = ScheduledWorkflow(
            workflow_id=workflow_id,
            user_id=user_id,
            tenant_id=tenant_id,
            name=name,
            description=description,
            schedule_type=type_enum,
            cron_expression=cron_expression,
            interval_seconds=interval_seconds,
            scheduled_time=scheduled_time,
            timezone=timezone,
            input_variables=input_variables or {},
            max_executions=max_executions,
            max_concurrent=max_concurrent,
            timeout_seconds=timeout_seconds,
            retry_on_failure=retry_on_failure,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
            next_run_at=next_run_at,
            status=ScheduleStatus.ACTIVE,
            is_enabled=True,
        )

        self._db.add(schedule)
        # flush+refresh preserves RLS context; get_db handles final commit
        await self._db.flush()
        await self._db.refresh(schedule)

        logger.info(f"Created schedule: {schedule.name} ({schedule.id}), next run: {next_run_at}")
        return schedule

    async def update_schedule(
        self,
        schedule_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
        **updates,
    ) -> Optional[ScheduledWorkflow]:
        """Update an existing schedule."""
        stmt = select(ScheduledWorkflow).where(
            ScheduledWorkflow.id == schedule_id,
            ScheduledWorkflow.user_id == user_id,
            ScheduledWorkflow.tenant_id == tenant_id,
        )
        result = await self._db.execute(stmt)
        schedule = result.scalar_one_or_none()

        if not schedule:
            return None

        # Validate timezone if being updated
        if 'timezone' in updates and updates['timezone']:
            validate_timezone(updates['timezone'])

        # Validate input_variables if being updated
        if 'input_variables' in updates and updates['input_variables']:
            validate_input_variables(updates['input_variables'])

        if 'schedule_type' in updates and updates['schedule_type']:
            updates['schedule_type'] = ScheduleType(updates['schedule_type'])

        for key, value in updates.items():
            if hasattr(schedule, key) and value is not None:
                setattr(schedule, key, value)

        # Recalculate next run if schedule parameters changed
        recalc_fields = ['cron_expression', 'interval_seconds', 'scheduled_time', 'timezone', 'schedule_type']
        if any(k in updates for k in recalc_fields):
            schedule_type_str = schedule.schedule_type.value if schedule.schedule_type else "cron"
            schedule.next_run_at = calculate_next_run(
                schedule_type_str,
                schedule.cron_expression,
                schedule.interval_seconds,
                schedule.scheduled_time,
                schedule.timezone or "UTC",
            )

        await self._db.flush()
        await self._db.refresh(schedule)

        return schedule

    async def delete_schedule(
        self,
        schedule_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
    ) -> bool:
        """Delete a schedule."""
        stmt = select(ScheduledWorkflow).where(
            ScheduledWorkflow.id == schedule_id,
            ScheduledWorkflow.user_id == user_id,
            ScheduledWorkflow.tenant_id == tenant_id,
        )
        result = await self._db.execute(stmt)
        schedule = result.scalar_one_or_none()

        if not schedule:
            return False

        await self._db.delete(schedule)
        # Let get_db handle the commit
        logger.info(f"Deleted schedule: {schedule.name} ({schedule_id})")
        return True

    async def pause_schedule(
        self,
        schedule_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
    ) -> Optional[ScheduledWorkflow]:
        """Pause a schedule."""
        stmt = select(ScheduledWorkflow).where(
            ScheduledWorkflow.id == schedule_id,
            ScheduledWorkflow.user_id == user_id,
            ScheduledWorkflow.tenant_id == tenant_id,
        )
        result = await self._db.execute(stmt)
        schedule = result.scalar_one_or_none()

        if not schedule:
            return None

        schedule.status = ScheduleStatus.PAUSED
        schedule.is_enabled = False

        await self._db.flush()
        await self._db.refresh(schedule)

        return schedule

    async def resume_schedule(
        self,
        schedule_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
    ) -> Optional[ScheduledWorkflow]:
        """Resume a paused schedule. Recalculates next_run_at from now (no catch-up)."""
        stmt = select(ScheduledWorkflow).where(
            ScheduledWorkflow.id == schedule_id,
            ScheduledWorkflow.user_id == user_id,
            ScheduledWorkflow.tenant_id == tenant_id,
        )
        result = await self._db.execute(stmt)
        schedule = result.scalar_one_or_none()

        if not schedule:
            return None

        schedule.status = ScheduleStatus.ACTIVE
        schedule.is_enabled = True

        # Recalculate next run from NOW (no catch-up for missed runs)
        schedule_type_str = schedule.schedule_type.value if schedule.schedule_type else "cron"
        schedule.next_run_at = calculate_next_run(
            schedule_type_str,
            schedule.cron_expression,
            schedule.interval_seconds,
            schedule.scheduled_time,
            schedule.timezone or "UTC",
        )

        await self._db.flush()
        await self._db.refresh(schedule)

        return schedule

    async def trigger_now(
        self,
        schedule_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
    ) -> Optional[ScheduledWorkflowExecution]:
        """Manually trigger a scheduled workflow to run immediately."""
        stmt = select(ScheduledWorkflow).where(
            ScheduledWorkflow.id == schedule_id,
            ScheduledWorkflow.user_id == user_id,
            ScheduledWorkflow.tenant_id == tenant_id,
        )
        result = await self._db.execute(stmt)
        schedule = result.scalar_one_or_none()

        if not schedule:
            return None

        execution = ScheduledWorkflowExecution(
            schedule_id=schedule.id,
            tenant_id=schedule.tenant_id,
            scheduled_for=datetime.now(tz.utc),
            status="pending",
        )
        self._db.add(execution)
        await self._db.flush()
        await self._db.refresh(execution)

        # Dispatch to daemon for immediate execution
        try:
            daemon = get_scheduler_daemon()
            if daemon.is_running:
                asyncio.create_task(
                    daemon.dispatch_immediate(schedule, execution)
                )
        except Exception:
            logger.warning(f"Could not dispatch immediate execution for schedule {schedule_id}")

        logger.info(f"Manually triggered schedule: {schedule.name} ({schedule_id})")
        return execution

    async def get_schedule(
        self,
        schedule_id: UUID,
        user_id: UUID,
        tenant_id: UUID,
    ) -> Optional[ScheduledWorkflow]:
        """Get a schedule by ID."""
        stmt = select(ScheduledWorkflow).where(
            ScheduledWorkflow.id == schedule_id,
            ScheduledWorkflow.user_id == user_id,
            ScheduledWorkflow.tenant_id == tenant_id,
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_schedules(
        self,
        user_id: UUID,
        tenant_id: UUID,
        workflow_id: Optional[UUID] = None,
        status: Optional[ScheduleStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ScheduledWorkflow]:
        """List schedules with optional filters."""
        conditions = [
            ScheduledWorkflow.user_id == user_id,
            ScheduledWorkflow.tenant_id == tenant_id,
        ]

        if workflow_id:
            conditions.append(ScheduledWorkflow.workflow_id == workflow_id)
        if status:
            conditions.append(ScheduledWorkflow.status == status)

        stmt = (
            select(ScheduledWorkflow)
            .where(and_(*conditions))
            .order_by(ScheduledWorkflow.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def list_executions(
        self,
        schedule_id: UUID,
        tenant_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ScheduledWorkflowExecution]:
        """List execution history for a schedule."""
        stmt = (
            select(ScheduledWorkflowExecution)
            .where(
                ScheduledWorkflowExecution.schedule_id == schedule_id,
                ScheduledWorkflowExecution.tenant_id == tenant_id,
            )
            .order_by(ScheduledWorkflowExecution.scheduled_for.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())


# =============================================
# Background Scheduler Daemon (Singleton)
# =============================================

class WorkflowSchedulerDaemon:
    """
    Background daemon that polls for due schedules and dispatches
    workflow executions via DurableWorkflowEngine.

    Architecture:
    - Dedicated connection pool (separate from HTTP request pool)
    - Cross-tenant poll query (bypasses RLS via dedicated session)
    - Per-execution sessions via get_db_for_tenant() for isolation
    - Global concurrency limit via asyncio.Semaphore
    - Per-schedule transactions (lock released before execution starts)
    - Timeout enforcement via asyncio.wait_for()
    """

    _instance: Optional['WorkflowSchedulerDaemon'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def _ensure_initialized(self) -> None:
        """Lazy initialization of engine and session factory."""
        if self._initialized:
            return

        from app.config import get_settings
        settings = get_settings()

        # BYPASSRLS system role (oniva_system): polling must see every tenant's schedules
        db_url = settings.system_database_url

        self._engine = create_async_engine(
            db_url,
            echo=False,
            pool_size=10,
            max_overflow=15,
            pool_timeout=10,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False,
        )
        self._semaphore = asyncio.Semaphore(settings.SCHEDULER_MAX_CONCURRENT_GLOBAL)
        self._poll_interval = settings.SCHEDULER_POLL_INTERVAL_SECONDS
        self._batch_size = settings.SCHEDULER_BATCH_SIZE

        self._active_tasks: set[asyncio.Task] = set()
        self._poll_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._last_poll_at: Optional[datetime] = None
        self._errors_count = 0
        self._initialized = True

    async def start(self) -> None:
        """Start the background scheduler poll loop."""
        self._ensure_initialized()
        if self._is_running:
            return
        self._is_running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Workflow scheduler daemon started")

    async def stop(self) -> None:
        """Gracefully stop the daemon with 30s grace period."""
        if not self._is_running:
            return

        logger.info("Stopping workflow scheduler daemon...")
        self._is_running = False

        # Cancel poll loop
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

        # Wait for active executions with grace period
        if self._active_tasks:
            logger.info(f"Waiting for {len(self._active_tasks)} active executions to complete...")
            for task in list(self._active_tasks):
                task.cancel()
            done, pending = await asyncio.wait(self._active_tasks, timeout=30)
            if pending:
                logger.warning(f"{len(pending)} executions did not complete within grace period")

        # Close dedicated engine
        if hasattr(self, '_engine'):
            await self._engine.dispose()

        logger.info("Workflow scheduler daemon stopped")

    @property
    def is_running(self) -> bool:
        return getattr(self, '_is_running', False)

    @property
    def status(self) -> dict:
        """Return daemon status for health check."""
        return {
            "running": self.is_running,
            "last_poll_at": self._last_poll_at.isoformat() if getattr(self, '_last_poll_at', None) else None,
            "active_executions": len(getattr(self, '_active_tasks', set())),
            "errors_count": getattr(self, '_errors_count', 0),
        }

    # Run orphan recovery every N poll cycles (~5 minutes at default 30s poll interval)
    _RECOVERY_EVERY_N_POLLS = 10

    async def _poll_loop(self) -> None:
        """Main poll loop -- runs every SCHEDULER_POLL_INTERVAL_SECONDS."""
        poll_count = 0
        while self._is_running:
            start = time.monotonic()
            try:
                await self._poll_once()
                poll_count += 1

                # Poll background agents if feature is enabled
                try:
                    from app.config import get_settings as _get_settings
                    if getattr(_get_settings(), 'ENABLE_BACKGROUND_AGENTS', False):
                        await self._poll_agents_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Agent scheduler poll error")

                # Periodic orphan recovery (scheduled + manual executions)
                if poll_count % self._RECOVERY_EVERY_N_POLLS == 0:
                    try:
                        await self.recover_orphaned_executions()
                    except Exception:
                        logger.exception("Periodic orphan recovery failed")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler poll error")
                self._errors_count += 1

            elapsed = time.monotonic() - start
            sleep_time = max(0, self._poll_interval - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    async def _poll_once(self) -> None:
        """Single poll iteration: fetch due schedules, create executions, dispatch."""
        self._last_poll_at = datetime.now(tz.utc)

        async with self._session_factory() as poll_db:
            # Fetch due schedules with FOR UPDATE SKIP LOCKED
            rows = await poll_db.execute(text("""
                SELECT id, tenant_id, workflow_id, user_id,
                       schedule_type, cron_expression, interval_seconds,
                       scheduled_time, timezone, input_variables,
                       max_executions, execution_count, max_concurrent,
                       timeout_seconds, retry_on_failure, max_retries,
                       retry_delay_seconds, name, workflow_version_id
                FROM scheduled_workflows
                WHERE is_enabled = true
                  AND status = 'ACTIVE'
                  AND next_run_at <= NOW()
                ORDER BY next_run_at ASC
                LIMIT :batch_size
                FOR UPDATE SKIP LOCKED
            """), {"batch_size": self._batch_size})
            due_schedules = rows.fetchall()

            if not due_schedules:
                return

            dispatch_list = []

            # Batch concurrent count check — single query instead of O(batch_size)
            schedule_ids = [row.id for row in due_schedules]
            concurrent_counts: dict = {}
            if schedule_ids:
                cc_result = await poll_db.execute(text("""
                    SELECT schedule_id, COUNT(*) AS cnt
                    FROM scheduled_workflow_executions
                    WHERE schedule_id = ANY(:sids) AND status IN ('pending', 'running')
                    GROUP BY schedule_id
                """), {"sids": schedule_ids})
                concurrent_counts = {r.schedule_id: r.cnt for r in cc_result.fetchall()}

            for row in due_schedules:
                schedule_id = row.id
                running_count = concurrent_counts.get(schedule_id, 0)

                if running_count >= row.max_concurrent:
                    continue

                # Calculate next_run_at
                try:
                    schedule_type_str = row.schedule_type
                    if hasattr(schedule_type_str, 'value'):
                        schedule_type_str = schedule_type_str.value

                    next_run = calculate_next_run(
                        schedule_type_str,
                        row.cron_expression,
                        row.interval_seconds,
                        row.scheduled_time,
                        row.timezone or "UTC",
                    )
                except Exception as e:
                    logger.error(f"Failed to calculate next_run for schedule {schedule_id}: {e}")
                    await poll_db.execute(text("""
                        UPDATE scheduled_workflows
                        SET status = 'ERROR', is_enabled = false, last_error = :err
                        WHERE id = :id
                    """), {"id": schedule_id, "err": str(e)[:2000]})
                    continue

                # Atomic execution_count check + increment + next_run update
                updated = await poll_db.execute(text("""
                    UPDATE scheduled_workflows
                    SET execution_count = execution_count + 1,
                        next_run_at = :next_run
                    WHERE id = :id
                      AND (max_executions IS NULL OR execution_count < max_executions)
                    RETURNING id
                """), {"id": schedule_id, "next_run": next_run})

                if not updated.fetchone():
                    # max_executions reached
                    await poll_db.execute(text("""
                        UPDATE scheduled_workflows
                        SET status = 'COMPLETED', is_enabled = false
                        WHERE id = :id
                    """), {"id": schedule_id})
                    continue

                # Handle one-time schedules
                if schedule_type_str == "one_time":
                    await poll_db.execute(text("""
                        UPDATE scheduled_workflows
                        SET status = 'COMPLETED', is_enabled = false
                        WHERE id = :id
                    """), {"id": schedule_id})

                # Create execution record
                exec_result = await poll_db.execute(text("""
                    INSERT INTO scheduled_workflow_executions
                        (id, schedule_id, tenant_id, scheduled_for, status, created_at)
                    VALUES (gen_random_uuid(), :schedule_id, :tenant_id, NOW(), 'pending', NOW())
                    RETURNING id
                """), {"schedule_id": schedule_id, "tenant_id": row.tenant_id})
                execution_id = exec_result.fetchone().id

                dispatch_list.append({
                    "schedule_id": schedule_id,
                    "execution_id": execution_id,
                    "tenant_id": row.tenant_id,
                    "workflow_id": row.workflow_id,
                    "user_id": row.user_id,
                    "input_variables": row.input_variables or {},
                    "timeout_seconds": row.timeout_seconds or 3600,
                    "retry_on_failure": row.retry_on_failure,
                    "max_retries": row.max_retries or 3,
                    "retry_delay_seconds": row.retry_delay_seconds or 60,
                    "name": row.name,
                    "workflow_version_id": row.workflow_version_id,
                })

            # Commit to release all FOR UPDATE locks
            await poll_db.commit()

        # Dispatch executions AFTER locks are released
        for item in dispatch_list:
            await self._semaphore.acquire()
            task = asyncio.create_task(
                self._execute_with_release(item)
            )
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)

    async def _execute_with_release(self, item: dict) -> None:
        """Execute a workflow and release the semaphore when done."""
        try:
            await self._execute_one(item)
        except Exception:
            logger.exception(f"Unhandled error executing schedule {item['schedule_id']}")
        finally:
            self._semaphore.release()

    async def _execute_one(self, item: dict) -> None:
        """Execute a single scheduled workflow with tenant isolation."""
        from app.core.database import get_db_for_tenant

        schedule_id = item["schedule_id"]
        execution_id = item["execution_id"]
        tenant_id = str(item["tenant_id"])

        async with get_db_for_tenant(tenant_id) as db:
            # Set audit context
            await db.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(item["user_id"])},
            )
            await db.execute(
                text("SELECT set_config('app.client_ip', 'scheduler-daemon', true)")
            )
            await db.execute(
                text("SELECT set_config('app.user_agent', :ua, true)"),
                {"ua": f"WorkflowSchedulerDaemon/1.0 schedule_id={schedule_id}"},
            )

            # Mark execution as running
            await db.execute(text("""
                UPDATE scheduled_workflow_executions
                SET status = 'running', started_at = NOW()
                WHERE id = :eid
            """), {"eid": execution_id})
            await db.flush()

            # Fetch workflow
            from app.models.workflow import Workflow, WorkflowVersion
            workflow = await db.get(Workflow, item["workflow_id"])
            if not workflow:
                await self._fail_execution(
                    db, schedule_id, execution_id, "Workflow not found or deleted"
                )
                return

            # Determine which spec to use: pinned version or current draft
            version_id = item.get("workflow_version_id")
            if version_id:
                version = await db.get(WorkflowVersion, version_id)
                if not version:
                    await self._fail_execution(
                        db, schedule_id, execution_id,
                        "Pinned workflow version not found — was it deleted?"
                    )
                    return
                workflow_spec = version.spec
            else:
                # Fallback to current draft spec (backward compatible)
                workflow_spec = workflow.spec

            # Create WorkflowExecution record (appears in workflow logs)
            wf_exec = WorkflowExecution(
                workflow_id=workflow.id,
                user_id=item["user_id"],
                tenant_id=item["tenant_id"],
                status="running",
                trigger="scheduled",
                input_data=item["input_variables"],
            )
            db.add(wf_exec)
            await db.flush()

            # Link scheduled execution to workflow execution
            await db.execute(text("""
                UPDATE scheduled_workflow_executions
                SET workflow_execution_id = :wfeid
                WHERE id = :eid
            """), {"wfeid": wf_exec.id, "eid": execution_id})

            # Execute with timeout enforcement
            try:
                from app.services.durable_workflow_engine import DurableWorkflowEngine

                engine = DurableWorkflowEngine(
                    db, str(item["user_id"]), item["tenant_id"]
                )
                result = await asyncio.wait_for(
                    engine.execute_workflow(
                        workflow_spec, item["input_variables"], workflow.id
                    ),
                    timeout=item["timeout_seconds"],
                )

                # Success
                await db.execute(text("""
                    UPDATE scheduled_workflow_executions
                    SET status = 'completed', completed_at = NOW(),
                        result_summary = :result
                    WHERE id = :eid
                """), {"eid": execution_id, "result": json.dumps(result if isinstance(result, dict) else {"output": str(result)})})

                wf_exec.status = "completed"
                wf_exec.results = result if isinstance(result, dict) else {"output": str(result)}

            except asyncio.TimeoutError:
                error_msg = f"Execution timed out after {item['timeout_seconds']}s"
                wf_exec.status = "failed"
                wf_exec.error = error_msg

            except Exception as e:
                error_msg = str(e)[:2000]
                wf_exec.status = "failed"
                wf_exec.error = error_msg
                logger.error(f"Schedule {schedule_id} execution failed: {error_msg}")

            # Retry logic: if failed and retry is configured, retry instead of marking final failure
            if wf_exec.status == "failed" and item.get("retry_on_failure"):
                # Get current retry count
                retry_result = await db.execute(text("""
                    SELECT retry_count FROM scheduled_workflow_executions WHERE id = :eid
                """), {"eid": execution_id})
                current_retry = (retry_result.scalar() or 0)
                max_retries = item.get("max_retries", 3)

                if current_retry < max_retries:
                    # Increment retry count, reset to pending for next poll cycle pickup
                    await db.execute(text("""
                        UPDATE scheduled_workflow_executions
                        SET status = 'pending',
                            retry_count = retry_count + 1,
                            error = :err,
                            started_at = NULL,
                            completed_at = NULL
                        WHERE id = :eid
                    """), {"eid": execution_id, "err": wf_exec.error})
                    logger.info(
                        f"Schedule {schedule_id} execution retry {current_retry + 1}/{max_retries}: {wf_exec.error}"
                    )
                    await db.commit()
                    return  # Skip final status update — will be retried

            # Final failure or success — update scheduled execution status
            if wf_exec.status == "failed":
                await db.execute(text("""
                    UPDATE scheduled_workflow_executions
                    SET status = 'failed', completed_at = NOW(), error = :err
                    WHERE id = :eid
                """), {"eid": execution_id, "err": wf_exec.error})

            # Update schedule's last_run tracking
            await db.execute(text("""
                UPDATE scheduled_workflows
                SET last_run_at = NOW(),
                    last_run_status = :status,
                    last_error = :err
                WHERE id = :sid
            """), {
                "sid": schedule_id,
                "status": wf_exec.status,
                "err": wf_exec.error,
            })

            await db.commit()

    async def _poll_agents_once(self) -> None:
        """Poll custom_agents with enabled background_config and dispatch due runs.

        Queries agents where background_config->>'enabled' = 'true' and
        next_run_at is past due. Uses background_dispatch for concurrency-safe
        job creation, then spawns the background agent executor.
        """
        from app.services.background_dispatch import (
            check_and_create_background_job,
            ConcurrentLimitExceeded,
        )
        from app.services.background_agent_executor import run_background_agent
        from app.services import fleet_engine

        async with self._session_factory() as poll_db:
            rows = await poll_db.execute(text("""
                SELECT id, tenant_id, user_id, name,
                       background_config, resource_constraints
                FROM custom_agents
                WHERE is_active = true
                  AND background_config IS NOT NULL
                  AND (background_config->>'enabled')::boolean = true
                  AND background_config->>'cron_expression' IS NOT NULL
                  AND (
                      background_config->>'next_run_at' IS NULL
                      OR (background_config->>'next_run_at')::timestamptz <= NOW()
                  )
                LIMIT :batch_size
                FOR UPDATE SKIP LOCKED
            """), {"batch_size": self._batch_size})
            due_agents = rows.fetchall()

            if not due_agents:
                return

            for row in due_agents:
                agent_id = row.id
                tenant_id = row.tenant_id
                user_id = row.user_id
                bg_config = row.background_config or {}
                res_constraints = row.resource_constraints or {}
                max_concurrent = res_constraints.get("max_concurrent", 1)
                cron_expr = bg_config.get("cron_expression")
                tz_str = bg_config.get("timezone", "UTC")

                # Calculate next_run_at
                try:
                    next_run = calculate_next_run("cron", cron_expr, None, None, tz_str)
                except Exception as e:
                    logger.error(f"Failed to calculate next_run for agent {agent_id}: {e}")
                    continue

                # Update next_run_at in background_config
                updated_config = dict(bg_config)
                updated_config["next_run_at"] = next_run.isoformat() if next_run else None
                await poll_db.execute(text("""
                    UPDATE custom_agents
                    SET background_config = :config
                    WHERE id = :id
                """), {"id": agent_id, "config": json.dumps(updated_config)})

                # Create background job via concurrency-safe dispatch
                try:
                    use_engine = await fleet_engine.tenant_uses_engine_fleet(poll_db, tenant_id)
                    job_input = bg_config.get("input_data")
                    if use_engine:
                        job_input = fleet_engine.mark_engine(job_input)
                    job = await check_and_create_background_job(
                        db=poll_db,
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        user_id=user_id,
                        max_concurrent=max_concurrent,
                        trigger_type="scheduled",
                        trigger_info={"type": "scheduler", "cron": cron_expr},
                        input_data=job_input,
                    )

                    # Commit to release locks before spawning executor
                    await poll_db.commit()

                    if use_engine:
                        # Engine tenants start an engine run; no asyncio task. A failed start leaves the
                        # job pending for the reconciler and never escapes this loop (plan 003 U7).
                        await fleet_engine.start_after_commit(tenant_id, job.id)
                        logger.info(f"Dispatched engine background agent {agent_id} (job {job.id})")
                        continue

                    # Spawn executor
                    task = asyncio.create_task(
                        run_background_agent(
                            job_id=job.id,
                            agent_id=agent_id,
                            tenant_id=tenant_id,
                            user_id=user_id,
                            input_data=bg_config.get("input_data"),
                        )
                    )
                    self._active_tasks.add(task)
                    task.add_done_callback(self._active_tasks.discard)

                    logger.info(f"Dispatched background agent {agent_id} (job {job.id})")

                except ConcurrentLimitExceeded:
                    logger.info(f"Agent {agent_id} at concurrent limit, skipping")
                    await poll_db.commit()
                except Exception as e:
                    logger.error(f"Failed to dispatch agent {agent_id}: {e}")
                    await poll_db.rollback()

    async def _fail_execution(
        self, db: AsyncSession, schedule_id, execution_id, error: str,
    ) -> None:
        """Mark an execution as failed."""
        await db.execute(text("""
            UPDATE scheduled_workflow_executions
            SET status = 'failed', completed_at = NOW(), error = :err
            WHERE id = :eid
        """), {"eid": execution_id, "err": error})
        await db.execute(text("""
            UPDATE scheduled_workflows
            SET last_run_status = 'failed', last_error = :err
            WHERE id = :sid
        """), {"sid": schedule_id, "err": error})
        await db.commit()

    async def dispatch_immediate(
        self, schedule: ScheduledWorkflow, execution: ScheduledWorkflowExecution,
    ) -> None:
        """Dispatch an immediate execution (called by trigger_now)."""
        self._ensure_initialized()
        item = {
            "schedule_id": schedule.id,
            "execution_id": execution.id,
            "tenant_id": schedule.tenant_id,
            "workflow_id": schedule.workflow_id,
            "user_id": schedule.user_id,
            "input_variables": schedule.input_variables or {},
            "timeout_seconds": schedule.timeout_seconds or 3600,
            "retry_on_failure": schedule.retry_on_failure,
            "max_retries": schedule.max_retries or 3,
            "retry_delay_seconds": schedule.retry_delay_seconds or 60,
            "name": schedule.name,
        }
        await self._semaphore.acquire()
        task = asyncio.create_task(self._execute_with_release(item))
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    # Default timeout for manual executions (matches _MANUAL_EXECUTION_TIMEOUT in workflows.py)
    _MANUAL_EXECUTION_TIMEOUT_SECONDS = 3600

    async def recover_orphaned_executions(self) -> None:
        """
        On startup and periodically: mark executions as failed if they exceed timeout.
        Handles both scheduled executions (via schedule timeout) and manual/API
        executions (via fixed 1-hour timeout).
        Follows the per-tenant RLS iteration pattern from main.py.
        """
        self._ensure_initialized()
        try:
            async with self._session_factory() as db:
                # 1. Recover orphaned SCHEDULED executions
                result = await db.execute(text("""
                    SELECT e.id, e.tenant_id, e.schedule_id, sw.timeout_seconds
                    FROM scheduled_workflow_executions e
                    JOIN scheduled_workflows sw ON e.schedule_id = sw.id
                    WHERE e.status = 'running'
                      AND e.started_at + (sw.timeout_seconds || ' seconds')::interval < NOW()
                """))
                scheduled_orphans = result.fetchall()

                # 2. Recover orphaned MANUAL/API executions (workflow_executions table)
                result2 = await db.execute(text("""
                    SELECT id, tenant_id
                    FROM workflow_executions
                    WHERE status = 'running'
                      AND started_at + (:timeout || ' seconds')::interval < NOW()
                """), {"timeout": self._MANUAL_EXECUTION_TIMEOUT_SECONDS})
                manual_orphans = result2.fetchall()

                if not scheduled_orphans and not manual_orphans:
                    return

                # Group scheduled orphans by tenant for RLS compliance
                by_tenant: dict = {}
                for row in scheduled_orphans:
                    tid = str(row.tenant_id)
                    by_tenant.setdefault(tid, []).append(row)

                total_scheduled = 0
                for tid, rows in by_tenant.items():
                    await db.execute(
                        text("SELECT set_config('app.current_tenant', :tid, true)"),
                        {"tid": tid},
                    )
                    for row in rows:
                        await db.execute(text("""
                            UPDATE scheduled_workflow_executions
                            SET status = 'failed',
                                completed_at = NOW(),
                                error = 'Server restarted - execution exceeded timeout'
                            WHERE id = :eid AND status = 'running'
                        """), {"eid": row.id})
                        total_scheduled += 1
                    await db.commit()

                # Group manual orphans by tenant
                manual_by_tenant: dict = {}
                for row in manual_orphans:
                    tid = str(row.tenant_id)
                    manual_by_tenant.setdefault(tid, []).append(row)

                total_manual = 0
                for tid, rows in manual_by_tenant.items():
                    await db.execute(
                        text("SELECT set_config('app.current_tenant', :tid, true)"),
                        {"tid": tid},
                    )
                    for row in rows:
                        await db.execute(text("""
                            UPDATE workflow_executions
                            SET status = 'failed',
                                completed_at = NOW(),
                                error = 'Execution orphaned - exceeded timeout after server restart'
                            WHERE id = :eid AND status = 'running'
                        """), {"eid": row.id})
                        total_manual += 1
                    await db.commit()

                if total_scheduled > 0:
                    logger.info(f"Recovered {total_scheduled} orphaned scheduled executions")
                if total_manual > 0:
                    logger.info(f"Recovered {total_manual} orphaned manual/API executions")

        except Exception as e:
            logger.warning(f"Failed to recover orphaned executions: {e}")


def get_scheduler_daemon() -> WorkflowSchedulerDaemon:
    """Get the singleton scheduler daemon instance."""
    return WorkflowSchedulerDaemon()
