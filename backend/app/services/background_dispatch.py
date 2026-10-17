"""Background agent dispatch service.

Handles creating BackgroundJob records with concurrent execution safety.
Shared by both the SchedulerDaemon and WebhookTriggerService to prevent
race conditions (TOCTOU) when the same agent is triggered concurrently.
"""

import logging
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.background_job import BackgroundJob, JobStatus, JobType, JobTrigger

logger = logging.getLogger(__name__)


class ConcurrentLimitExceeded(Exception):
    """Raised when an agent has reached its max concurrent execution limit."""
    pass


async def check_and_create_background_job(
    db: AsyncSession,
    tenant_id: UUID,
    agent_id: UUID,
    user_id: UUID,
    max_concurrent: int,
    trigger_type: str,
    trigger_info: dict,
    input_data: dict | None = None,
    job_id: UUID | None = None,
) -> BackgroundJob:
    """Atomically check concurrent limit and create a BackgroundJob.

    Uses a per-agent advisory lock to prevent race conditions between
    scheduler dispatch and webhook dispatch for the same agent.

    Args:
        db: Database session (must be within a transaction)
        tenant_id: Tenant UUID
        agent_id: Agent UUID
        user_id: User UUID (agent creator for system-triggered jobs)
        max_concurrent: Maximum concurrent runs allowed
        trigger_type: "scheduled" | "webhook" | "manual"
        trigger_info: {"type": "scheduler", "schedule_id": "..."} or similar
        input_data: Optional input parameters for the agent run
        job_id: Optional deterministic job id (webhooks derive it from trigger and idempotency key)

    Returns:
        Created BackgroundJob

    Raises:
        ConcurrentLimitExceeded: If agent is at max concurrent runs
    """
    # Serialize dispatch per agent. Postgres rejects SELECT count(*) ... FOR UPDATE ("FOR UPDATE
    # is not allowed with aggregate functions"), so take a transaction-scoped advisory lock keyed
    # on the agent instead; it is released at commit/rollback. The count below then runs as a new
    # statement (READ COMMITTED snapshot), so it sees jobs committed by the previous lock holder.
    # Other dialects (SQLite in the unit tier) have no row/advisory locks and serialize writers.
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"background_dispatch:{agent_id}"},
        )

    running_count_result = await db.execute(
        select(func.count())
        .select_from(BackgroundJob)
        .where(
            BackgroundJob.agent_id == agent_id,
            BackgroundJob.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]),
        )
    )
    running_count = running_count_result.scalar() or 0

    if running_count >= max_concurrent:
        raise ConcurrentLimitExceeded(
            f"Agent {agent_id} at max concurrent runs: {running_count}/{max_concurrent}"
        )

    # Create the job in the same transaction (lock prevents races)
    job = BackgroundJob(
        **({"id": job_id} if job_id is not None else {}),
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=agent_id,
        job_type=JobType.BACKGROUND_AGENT.value,
        status=JobStatus.PENDING.value,
        trigger=trigger_type,
        triggered_by=trigger_info,
        input_data=input_data,
    )
    db.add(job)
    await db.flush()

    logger.info(
        f"Background job created: {job.id} for agent {agent_id} "
        f"(trigger={trigger_type}, concurrent={running_count + 1}/{max_concurrent})"
    )

    return job
