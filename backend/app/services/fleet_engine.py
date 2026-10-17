"""Start Fleet runs on the engine (fleet-workflows-on-engine plan, U7).

A Fleet run is an engine conversation plus one submission (``requestId = fleet:{job_id}``); there is
no asyncio task and no process state. ``start_fleet_run`` is idempotent: the conversation id is
deterministic (``automation_scope``), a repeated create answers 409 and a repeated submit with the
same key is deduplicated by the engine, so a retry from the reconciler never doubles anything.

A start that cannot reach the engine leaves the job ``pending`` with ``error`` set and a
``start_attempts`` counter in ``input_data``; the ``fleet`` ``start_missing`` hook re-runs the start
and the registry (``run_handlers``) owns the bound and the backoff.

Registers the ``fleet`` ``start_missing``, ``find_stuck`` and ``fail`` hooks (``on_finished`` is U9).
"""
import functools
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from uuid import UUID

from sqlalchemy import select

from app.core.database import get_db_for_tenant
from app.models.background_job import BackgroundJob, JobStatus, JobType
from app.models.custom_agent import CustomAgent
from app.models.tenant import Tenant
from app.services import run_handlers
from app.services.automation_scope import bind_run_conversation
from app.services.engine_client import EngineError, get_engine_client
from app.services.feature_flags import engine_fleet_enabled
from app.services.fleet_engine_agent import (
    build_fleet_agent_config,
    wants_connector_summary,
    _preload_connector_summary,
)

logger = logging.getLogger(__name__)

KIND = "fleet"
ENGINE_MARK = "oniva-engine"  # background_jobs.input_data["engine"] for jobs that run on the engine
_ENGINE_KEYS = ("engine", "start_attempts")
STUCK_BATCH = 200


def tenant_factory(tenant_id: Any) -> Callable:
    """Own tenant-scoped session per call; tests replace this with their database."""
    return functools.partial(get_db_for_tenant, str(tenant_id))


def request_id(job_id: Any) -> str:
    return f"fleet:{job_id}"


def _uuid(value: Any) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


async def tenant_uses_engine_fleet(db, tenant_id: Any) -> bool:
    """True when this tenant's Fleet runs start on the engine. Any failure reads as off."""
    try:
        tenant = await db.get(Tenant, _uuid(tenant_id))
        return tenant is not None and engine_fleet_enabled(tenant) is True
    except Exception:
        logger.warning("engine fleet flag lookup failed for tenant %s", tenant_id, exc_info=True)
        return False


def mark_engine(input_data: Optional[dict]) -> dict:
    """``input_data`` flagged as an engine job, set at insert so a crash before the start is still found."""
    return {**(input_data or {}), "engine": ENGINE_MARK}


def task_text(input_data: Optional[dict]) -> str:
    """The task the agent is asked to do (same rule as the legacy executor)."""
    data = {k: v for k, v in (input_data or {}).items() if k not in _ENGINE_KEYS}
    text = ""
    if data:
        text = data.get("task", data.get("message", json.dumps(data)))
    return text or "Execute your configured task."


async def load_agent(db, agent_id: Any) -> Optional[CustomAgent]:
    return await db.get(CustomAgent, agent_id) if agent_id else None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _attempts(job: BackgroundJob) -> int:
    try:
        return int((job.input_data or {}).get("start_attempts") or 0)
    except (TypeError, ValueError):
        return 0


async def _record_failure(factory: Callable, job_id: UUID, error: str) -> int:
    """Leave the job pending with the error and one more start attempt counted; returns the count."""
    async with factory() as db:
        job = await db.get(BackgroundJob, job_id)
        if job is None or job.status != JobStatus.PENDING.value:
            return 0
        attempts = _attempts(job) + 1
        job.input_data = {**mark_engine(job.input_data), "start_attempts": attempts}
        job.error = error[:2000]
        return attempts


async def start_fleet_run(factory: Callable, job_id: Any, *, engine: Any = None,
                          now_ms: Callable[[], int] = _now_ms) -> run_handlers.StartResult:
    """Start (or idempotently re-start) the engine run of a Fleet job. Never raises for engine trouble."""
    job_id = _uuid(job_id)
    async with factory() as db:
        job = await db.get(BackgroundJob, job_id)
        if job is None:
            return run_handlers.StartResult.failed("job_not_found")
        if job.status != JobStatus.PENDING.value or job.conversation_id is not None:
            # Running (started), or already terminal: nothing to start.
            return run_handlers.StartResult.already_started()
        agent = await load_agent(db, job.agent_id)
        tenant_id, user_id, input_data = job.tenant_id, job.user_id, job.input_data
        if agent is None:
            return run_handlers.StartResult.failed("agent_not_found")
        connector_summary = None
        if wants_connector_summary(agent.skills or []):
            connector_summary = await _preload_connector_summary(str(user_id), str(tenant_id))
        built = build_fleet_agent_config(agent, job_id, now_ms(), connector_summary=connector_summary)

    for warning in built.warnings:
        logger.warning("fleet job %s: %s", job_id, warning)
    engine = engine or get_engine_client()
    key = request_id(job_id)
    try:
        conversation_id, project_id, session_id = await bind_run_conversation(
            factory, KIND, job_id, tenant_id, user_id)
        try:
            await engine.create_conversation(session_id, tenant_id, project_id, conversation_id, built.config)
        except EngineError as e:
            if e.status != 409:  # 409: the conversation exists from an earlier try
                raise
        sub = await engine.submit(session_id, tenant_id, project_id, conversation_id,
                                  {"text": task_text(input_data), "requestId": key}, key)
    except (EngineError, OSError, TimeoutError) as e:
        error = f"engine_unavailable: {e}"
        logger.warning("fleet job %s could not start on the engine: %s", job_id, e)
        attempts = await _record_failure(factory, job_id, error)
        return run_handlers.StartResult.failed(error, attempts=attempts)

    async with factory() as db:
        job = await db.get(BackgroundJob, job_id)
        if job is not None and job.status == JobStatus.PENDING.value:
            job.conversation_id = conversation_id
            job.session_id = str(job.id)
            job.input_data = {**mark_engine(job.input_data)}
            job.input_data.pop("start_attempts", None)
            job.error = None
            job.status = JobStatus.RUNNING.value
            job.started_at = datetime.now(timezone.utc)
    task_id = sub.get("taskId") if isinstance(sub, dict) else None
    return run_handlers.StartResult.started(task_id)


async def start_after_commit(tenant_id: Any, job_id: Any, *, factory: Optional[Callable] = None) -> None:
    """Dispatch-site helper: start the engine run of a just-committed job; nothing escapes."""
    try:
        await start_fleet_run(factory or tenant_factory(tenant_id), job_id)
    except Exception:
        logger.exception("fleet job %s: engine start failed unexpectedly", job_id)


# ---- registry hooks ------------------------------------------------------------------------------


def _ref(job: BackgroundJob) -> run_handlers.RunRef:
    return run_handlers.RunRef(kind=KIND, ref_id=job.id, tenant_id=job.tenant_id,
                               session_id=job.session_id, conversation_id=job.conversation_id)


async def find_stuck(session, older_than: timedelta) -> list:
    """Pending engine jobs that never got a conversation recorded and have been quiet long enough."""
    cutoff = datetime.now(timezone.utc) - older_than
    rows = (await session.execute(
        select(BackgroundJob).where(
            BackgroundJob.status == JobStatus.PENDING.value,
            BackgroundJob.job_type == JobType.BACKGROUND_AGENT.value,
            BackgroundJob.conversation_id.is_(None),
            BackgroundJob.created_at < cutoff,
        ).order_by(BackgroundJob.created_at).limit(STUCK_BATCH)
    )).scalars().all()
    return [_ref(j) for j in rows if (j.input_data or {}).get("engine") == ENGINE_MARK]


async def start_missing(ref: run_handlers.RunRef) -> run_handlers.StartResult:
    if ref.tenant_id is None:
        return run_handlers.StartResult.failed("tenant_unknown")
    return await start_fleet_run(tenant_factory(ref.tenant_id), ref.ref_id)


async def fail(ref: run_handlers.RunRef, code: str) -> None:
    if ref.tenant_id is None:
        return
    async with tenant_factory(ref.tenant_id)() as db:
        job = await db.get(BackgroundJob, ref.ref_id)
        if job is not None and job.status in (JobStatus.PENDING.value, JobStatus.RUNNING.value):
            job.fail(code)


def register_hooks() -> None:
    run_handlers.register(KIND, start_missing=start_missing, find_stuck=find_stuck, fail=fail)


register_hooks()
