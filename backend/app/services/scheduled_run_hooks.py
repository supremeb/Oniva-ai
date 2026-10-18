"""Scheduled workflow runs on the engine (fleet-workflows-on-engine plan, U19).

``start_scheduled`` turns one claimed ``scheduled_workflow_executions`` row into an engine run: it creates the
``WorkflowExecution`` and starts ``workflow_run`` with run-level retry (``retry = {max, delayMs}``, U35) and the
schedule's timeout as the run deadline. The ids are deterministic (the request key is ``scheduled:{id}``), so the
poll loop, ``trigger_now`` and the reconciler can all call it and never start a second run. Everything after the
start (status, ``retry_count``, ``last_run_*``) is written by the finalize path (``workflow_run_sync``).

Registers the ``scheduled`` kind's ``start_missing``, ``find_stuck``, ``fail`` and a safety-net ``on_finished``. A scheduled execution is
stuck when it was claimed (``pending``/``running``) but its ``WorkflowExecution`` was never created; once that row
exists the ``workflow`` kind owns it (start retries, ``fail``) and finishes this row with it.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.scheduled_workflow import ScheduledWorkflow, ScheduledWorkflowExecution
from app.models.workflow import Workflow, WorkflowExecution, WorkflowVersion
from app.services import run_handlers
from app.services import workflow_engine_runs as wer

logger = logging.getLogger(__name__)

KIND = "scheduled"
STUCK_BATCH = 200
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY_S = 60
DEFAULT_TIMEOUT_S = 3600


def request_key(scheduled_execution_id: Any) -> str:
    return f"scheduled:{scheduled_execution_id}"


def run_options(schedule: ScheduledWorkflow, now_ms: Optional[int] = None) -> tuple[dict, int]:
    """``(retry, deadline_ms)`` of a scheduled run: the schedule's retry policy and its timeout as an absolute deadline."""
    max_retries = DEFAULT_MAX_RETRIES if schedule.max_retries is None else int(schedule.max_retries)
    delay_s = DEFAULT_RETRY_DELAY_S if schedule.retry_delay_seconds is None else int(schedule.retry_delay_seconds)
    timeout_s = int(schedule.timeout_seconds or DEFAULT_TIMEOUT_S)
    retry = {"max": max_retries if schedule.retry_on_failure else 0, "delayMs": delay_s * 1000}
    now = int(time.time() * 1000) if now_ms is None else now_ms
    return retry, now + timeout_s * 1000


async def _terminal(db, row: ScheduledWorkflowExecution, schedule: Optional[ScheduledWorkflow], error: str) -> None:
    """End a scheduled execution that can never start (missing workflow, invalid spec, start bound reached)."""
    now = datetime.now(timezone.utc)
    row.status = "failed"
    row.completed_at = now
    row.error = error[:2000]
    if schedule is not None:
        schedule.last_run_at = now
        schedule.last_run_status = "failed"
        schedule.last_error = error[:2000]
    await db.commit()


def _bump_attempts(row: ScheduledWorkflowExecution) -> int:
    summary = dict(row.result_summary or {})
    summary["start_attempts"] = int(summary.get("start_attempts") or 0) + 1
    row.result_summary = summary
    return summary["start_attempts"]


async def start_scheduled(db, scheduled_execution_id: Any, *, factory: Optional[Callable] = None,
                          engine: Any = None) -> run_handlers.StartResult:
    """Start (or idempotently re-start) the engine run of one scheduled execution. Never raises for engine trouble.

    ``db`` is a tenant session; this commits it (the start does). A run whose start did not reach the engine stays
    ``queued`` for the reconciler.
    """
    row = await db.get(ScheduledWorkflowExecution, wer._uuid(scheduled_execution_id), with_for_update=True)
    if row is None:
        return run_handlers.StartResult.failed("scheduled_execution_not_found")
    if row.status in ("completed", "failed", "skipped"):
        return run_handlers.StartResult.already_started()
    schedule = await db.get(ScheduledWorkflow, row.schedule_id)
    if schedule is None:
        await _terminal(db, row, None, "Schedule not found or deleted")
        return run_handlers.StartResult.already_started()
    workflow = await db.get(Workflow, schedule.workflow_id)
    if workflow is None:
        await _terminal(db, row, schedule, "Workflow not found or deleted")
        return run_handlers.StartResult.already_started()
    spec = workflow.spec
    if schedule.workflow_version_id:
        version = await db.get(WorkflowVersion, schedule.workflow_version_id)
        if version is None:
            await _terminal(db, row, schedule, "Pinned workflow version not found — was it deleted?")
            return run_handlers.StartResult.already_started()
        spec = version.spec

    exec_id = wer.execution_id_for(schedule.tenant_id, workflow.id, request_key(row.id))
    row.status = "running"
    row.started_at = row.started_at or datetime.now(timezone.utc)
    retry, deadline_ms = run_options(schedule)
    row_id, tenant_id = row.id, schedule.tenant_id
    try:
        execution, _ = await wer.start_workflow_run(
            db, workflow, spec, dict(schedule.input_variables or {}), user_id=schedule.user_id,
            tenant_id=schedule.tenant_id, trigger="scheduled", request_key=request_key(row.id),
            version_id=schedule.workflow_version_id, factory=factory, engine=engine,
            deadline_ms=deadline_ms, retry=retry, settle_on_failure=False)
    except HTTPException as exc:
        if exc.status_code == 400:  # an invalid workflow never starts
            await db.rollback()
            row = await db.get(ScheduledWorkflowExecution, row_id)
            schedule = await db.get(ScheduledWorkflow, schedule.id)
            await _terminal(db, row, schedule, f"invalid_workflow: {exc.detail}")
            return run_handlers.StartResult.already_started()
        # The engine did not take the start. The queued execution exists now; link it and count the try.
        code = (exc.detail or {}).get("error", "engine_unreachable") if isinstance(exc.detail, dict) else "engine_unreachable"
        row = await db.get(ScheduledWorkflowExecution, row_id)
        row.workflow_execution_id = exec_id
        attempts = _bump_attempts(row)
        await db.commit()
        logger.warning("scheduled execution %s: engine start failed (%s), attempt %d", row_id, code, attempts)
        return run_handlers.StartResult.failed(code, attempts)
    row = await db.get(ScheduledWorkflowExecution, row_id)
    if row is not None and row.workflow_execution_id is None:
        row.workflow_execution_id = execution.id
        if execution.engine_conversation_id is not None:
            row.result_summary = {**(row.result_summary or {}),
                                  "engine_conversation_id": str(execution.engine_conversation_id)}
    if row is not None and execution.status in wer.TERMINAL:  # a replay of a run that already ended
        from app.services import workflow_run_sync  # lazy: it imports this module's neighbours
        await workflow_run_sync.finish_scheduled(db, execution, execution.status)
    await db.commit()
    logger.info("scheduled execution %s started engine run %s (tenant %s)", row_id, execution.id, tenant_id)
    return run_handlers.StartResult.started()


# ---- registry hooks -----------------------------------------------------------------------------


def _ref(row: ScheduledWorkflowExecution) -> run_handlers.RunRef:
    return run_handlers.RunRef(kind=KIND, ref_id=row.id, tenant_id=row.tenant_id)


async def _linked_terminal(session, cutoff, quiet) -> list:
    """Claimed rows whose linked execution already ended: a lost finish (e.g. a cancel) that never reached the row."""
    rows = (await session.execute(
        select(ScheduledWorkflowExecution)
        .join(WorkflowExecution, WorkflowExecution.id == ScheduledWorkflowExecution.workflow_execution_id)
        .where(ScheduledWorkflowExecution.status.in_(("pending", "running")),
               WorkflowExecution.status.in_(wer.TERMINAL),
               quiet < cutoff)
        .order_by(quiet).limit(STUCK_BATCH)
    )).scalars().all()
    return [_ref(r) for r in rows]


async def find_stuck(session, older_than: timedelta) -> list:
    """Claimed executions (``pending``/``running``) of engine tenants with no ``WorkflowExecution`` yet."""
    cutoff = datetime.now(timezone.utc) - older_than
    quiet = func.coalesce(ScheduledWorkflowExecution.started_at, ScheduledWorkflowExecution.created_at)
    candidates = (await session.execute(
        select(ScheduledWorkflowExecution, ScheduledWorkflow.workflow_id)
        .join(ScheduledWorkflow, ScheduledWorkflow.id == ScheduledWorkflowExecution.schedule_id)
        .where(ScheduledWorkflowExecution.status.in_(("pending", "running")),
               ScheduledWorkflowExecution.workflow_execution_id.is_(None),  # a legacy run links its execution
               quiet < cutoff)
        .order_by(quiet).limit(STUCK_BATCH)
    )).all()
    linked = await _linked_terminal(session, cutoff, quiet)
    if not candidates:
        return linked
    ids = {row.id: wer.execution_id_for(row.tenant_id, workflow_id, request_key(row.id))
           for row, workflow_id in candidates}
    started = set((await session.execute(
        select(WorkflowExecution.id).where(WorkflowExecution.id.in_(list(ids.values())))
    )).scalars())
    flags: dict = {}
    refs = []
    for row, _ in candidates:
        if ids[row.id] in started:
            continue  # the workflow kind owns it now
        if row.tenant_id not in flags:
            flags[row.tenant_id] = await wer.tenant_uses_engine_workflows(session, row.tenant_id)
        if flags[row.tenant_id]:
            refs.append(_ref(row))
    return refs + linked


async def start_missing(ref: run_handlers.RunRef) -> run_handlers.StartResult:
    if ref.tenant_id is None:
        return run_handlers.StartResult.failed("tenant_unknown")
    async with wer.tenant_factory(ref.tenant_id)() as db:
        row = await db.get(ScheduledWorkflowExecution, ref.ref_id)
        if row is not None and row.workflow_execution_id is not None and row.status in ("pending", "running"):
            execution = await db.get(WorkflowExecution, row.workflow_execution_id)
            if execution is not None and execution.status in wer.TERMINAL:  # a finish that never reached the row
                from app.services import workflow_run_sync
                await workflow_run_sync.finish_scheduled(db, execution, execution.status)
                await db.commit()
                return run_handlers.StartResult.already_started()
        return await start_scheduled(db, ref.ref_id)


async def fail(ref: run_handlers.RunRef, code: str) -> None:
    if ref.tenant_id is None:
        return
    async with wer.tenant_factory(ref.tenant_id)() as db:
        row = await db.get(ScheduledWorkflowExecution, ref.ref_id)
        if row is None or row.status in ("completed", "failed", "skipped"):
            return
        schedule = await db.get(ScheduledWorkflow, row.schedule_id)
        await _terminal(db, row, schedule, code)


async def on_finished(ref: run_handlers.RunRef, snapshot: dict) -> None:
    """Safety net: end a scheduled row whose linked execution already ended (the finalize never reached it)."""
    if ref.tenant_id is None:
        return
    from app.services import workflow_run_sync
    async with wer.tenant_factory(ref.tenant_id)() as db:
        row = await db.get(ScheduledWorkflowExecution, ref.ref_id)
        if row is None or row.workflow_execution_id is None or row.status in ("completed", "failed", "skipped"):
            return
        execution = await db.get(WorkflowExecution, row.workflow_execution_id)
        if execution is not None and execution.status in wer.TERMINAL:
            await workflow_run_sync.finish_scheduled(db, execution, execution.status)


def register_hooks() -> None:
    run_handlers.register(KIND, on_finished=on_finished, start_missing=start_missing, find_stuck=find_stuck, fail=fail)


register_hooks()
