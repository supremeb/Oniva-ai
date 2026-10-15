"""
Deep Work API — Autonomous agent execution with plan-first UX.

Endpoints for starting, controlling, and observing Deep Work sessions.

Two execution modes:
1. AutonomousExecutor (legacy): POST /deep-work/execute — single-agent sequential loop
2. Orchestrator-Worker Engine (v2): POST /deep-work/engine — multi-agent parallel pipeline
"""

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth.dependencies import get_current_user_id, get_current_tenant_id
from app.config import get_settings
from app.core.database import get_db, AsyncSessionLocal, set_tenant_context
from app.api.v1 import deep_work_engine as engine_dw
from app.services import deep_work_state as dws
from app.models.background_job import BackgroundJob, JobStatus, JobType, JobTrigger

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PlanSchema(BaseModel):
    title: str
    steps: List[str]


class ExecuteRequest(BaseModel):
    agent_id: Optional[str] = None
    plan: PlanSchema
    message: str
    conversation_id: Optional[str] = None


class ExecuteResponse(BaseModel):
    session_id: str
    job_id: str


class HumanInputRequest(BaseModel):
    input: str


class TodoSchema(BaseModel):
    text: str
    status: str = "pending"


class SourceSchema(BaseModel):
    type: str  # "search", "url", "knowledge_base"
    title: str = "Source"
    url: Optional[str] = None
    domain: Optional[str] = None
    query: Optional[str] = None


class ConnectorSchema(BaseModel):
    name: str
    type: str = "connector"


class SessionStatusResponse(BaseModel):
    session_id: str
    status: str
    current_step: int = 0
    total_steps: int = 0
    elapsed_seconds: int = 0
    # Full state for refresh recovery
    original_message: Optional[str] = None
    plan: Optional[dict] = None
    todos: List[TodoSchema] = []
    sources: List[SourceSchema] = []
    connectors: List[ConnectorSchema] = []
    summary: Optional[str] = None
    key_findings: List[str] = []


class WorkspaceFileResponse(BaseModel):
    name: str
    path: str
    size: int
    type: str
    modified_at: float = 0


class DeepWorkFullStateResponse(BaseModel):
    session_id: str
    conversation_id: str | None = None
    status: str
    plan: dict | None = None
    todos: list[TodoSchema] = []
    steps: list[dict] = []
    workspace_files: list[WorkspaceFileResponse] = []
    summary: str | None = None
    key_findings: list[str] = []
    sources: list[SourceSchema] = []
    connectors: list[ConnectorSchema] = []
    is_engine: bool = False
    engine_report: str | None = None
    engine_phase: str | None = None
    artifacts: list[dict] | None = None
    error: str | None = None
    workers: list[dict] | None = None


class SessionListItem(BaseModel):
    session_id: str
    conversation_id: str | None = None
    status: str
    plan_title: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    source: str = "legacy"  # "legacy" | "engine"


class EnginePlanResponse(BaseModel):
    title: str
    steps: list[str]
    raw_plan: dict
    session_id: str | None = None  # engine path only
    conversation_id: str | None = None  # engine path only


class DeepWorkEngineRequest(BaseModel):
    query: str = Field(..., min_length=5, max_length=5000)
    session_id: str | None = None
    conversation_id: str | None = None
    plan: dict | None = None  # Pre-approved plan from /engine/plan
    enriched_query: str | None = None  # Enriched query from intake clarification
    # Engine path only (ignored by the legacy pipeline):
    project_id: str | None = None
    knowledge_base_ids: list[str] = Field(default_factory=list)
    connector_ids: list[str] = Field(default_factory=list)


class ClarifyRequest(BaseModel):
    query: str = Field(..., min_length=5, max_length=5000)
    available_kbs: list[str] = Field(default_factory=list)
    available_connectors: list[str] = Field(default_factory=list)
    project_id: str | None = None  # Engine path only
    session_id: str | None = None  # Engine path only: reuse the session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_feature_enabled():
    if not getattr(settings, "ENABLE_DEEP_WORK", False):
        raise HTTPException(status_code=404, detail="Deep Work feature not enabled")


def _parse_uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    """Safely parse a string as UUID, returning None for non-UUID strings like 'general'."""
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


# In-memory executor registry (single-server; swap for Redis in production)
if TYPE_CHECKING:
    from app.services.autonomous_executor import AutonomousExecutor

_executors: dict[str, "AutonomousExecutor"] = {}
_executors_lock = asyncio.Lock()

# In-memory engine task registry (separate from _executors — coexistence)
_engine_tasks: dict[str, asyncio.Task] = {}


async def _verify_session_ownership(
    db: AsyncSession, session_id: str, tenant_id: UUID, user_id: UUID,
) -> BackgroundJob:
    """Verify the calling user owns this session. Returns job or raises 403."""
    result = await db.execute(
        select(BackgroundJob)
        .where(BackgroundJob.session_id == session_id)
        .where(BackgroundJob.tenant_id == tenant_id)
        .where(BackgroundJob.user_id == user_id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=403, detail="Access denied")
    return job


async def _get_general_assistant_config(
    db: AsyncSession, tenant_id: UUID,
) -> dict | None:
    """Load the General Assistant agent's config dict for model tier resolution."""
    from app.models.custom_agent import CustomAgent

    result = await db.execute(
        select(CustomAgent)
        .where(CustomAgent.tenant_id == tenant_id)
        .where(CustomAgent.name == "General Assistant")
        .where(CustomAgent.is_active.is_(True))
        .limit(1)
    )
    agent = result.scalar_one_or_none()
    if agent and agent.config:
        return agent.config
    return None


async def _update_job_status(
    db: AsyncSession, session_id: str, status: JobStatus,
    error_detail: str | None = None,
) -> None:
    """Update BackgroundJob status by session_id."""
    result = await db.execute(
        select(BackgroundJob).where(BackgroundJob.session_id == session_id)
    )
    job = result.scalar_one_or_none()
    if job:
        if status == JobStatus.COMPLETED:
            job.complete()
        elif status == JobStatus.FAILED:
            job.fail(error_detail or "Engine pipeline error")
        elif status == JobStatus.CANCELLED:
            job.cancel()
        await db.commit()


# ---------------------------------------------------------------------------
# POST /deep-work/execute — start autonomous execution
# ---------------------------------------------------------------------------

@router.post("/deep-work/execute", response_model=ExecuteResponse)
async def execute_deep_work(
    body: ExecuteRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Start a Deep Work session. Creates a BackgroundJob, initialises the
    AutonomousExecutor, and spawns the agent loop as an async task.
    Returns session_id + job_id immediately.
    """
    _check_feature_enabled()

    session_id = str(uuid.uuid4())

    # Persist a BackgroundJob for tracking
    job = BackgroundJob(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        job_type=JobType.DEEP_WORK.value,
        status=JobStatus.PENDING.value,
        trigger=JobTrigger.MANUAL.value,
        session_id=session_id,
        conversation_id=_parse_uuid(body.conversation_id),
        agent_id=_parse_uuid(body.agent_id),
        input_data={
            "plan": body.plan.model_dump(),
            "message": body.message,
            "conversation_id": body.conversation_id,
        },
    )
    db.add(job)
    job_id = str(job.id)
    await db.commit()

    # Spawn background execution
    asyncio.create_task(
        _run_deep_work(
            job_id=job_id,
            session_id=session_id,
            agent_id=body.agent_id,
            plan=body.plan.model_dump(),
            message=body.message,
            user_id=user_id,
            tenant_id=tenant_id,
            conversation_id=body.conversation_id,
        )
    )

    return ExecuteResponse(session_id=session_id, job_id=job_id)


async def _run_deep_work(
    job_id: str,
    session_id: str,
    agent_id: str,
    plan: dict,
    message: str,
    user_id: UUID,
    tenant_id: UUID,
    conversation_id: str | None = None,
):
    """Background task that drives the AutonomousExecutor."""
    from app.services.autonomous_executor import AutonomousExecutor
    from app.services.session_storage import get_session_storage
    from app.agents.skills.registry import SkillRegistry
    from app.services.llm_gateway import LLMGateway

    async with AsyncSessionLocal() as db:
        await set_tenant_context(db, str(tenant_id))
        try:
            # Mark job as running
            result = await db.execute(
                select(BackgroundJob).where(BackgroundJob.id == uuid.UUID(job_id))
            )
            job = result.scalar_one_or_none()
            if job:
                job.start()
                await db.commit()

            # Re-set tenant context (commit ends transaction-scoped setting)
            await set_tenant_context(db, str(tenant_id))

            # Load agent config
            agent_config = None
            agent_uuid = _parse_uuid(agent_id)
            if agent_uuid:
                from app.models.custom_agent import CustomAgent
                agent_result = await db.execute(
                    select(CustomAgent).where(CustomAgent.id == agent_uuid)
                )
                agent_config = agent_result.scalar_one_or_none()

            # Build executor
            sse_queue: asyncio.Queue = asyncio.Queue()
            executor = AutonomousExecutor(
                session_storage=get_session_storage(),
                skill_registry=SkillRegistry(),
                llm_gateway=LLMGateway(db),
                sse_queue=sse_queue,
            )

            # Store executor for control endpoints (lock prevents TOCTOU race)
            async with _executors_lock:
                if session_id in _executors:
                    raise HTTPException(409, "Session already active")
                _executors[session_id] = executor

            # Initialise session files
            todo_items = plan.get("steps", [])
            await executor.start_session(
                agent_config=agent_config,
                plan=plan,
                todo_items=todo_items,
                message=message,
                user_id=str(user_id),
                tenant_id=str(tenant_id),
                session_id=session_id,
                conversation_id=conversation_id,
            )

            # Store SSE queue for the events endpoint
            _sse_queues[session_id] = sse_queue

            # Run the agent loop
            await executor.execute(session_id)

            # Mark job complete (re-set tenant context after executor may have committed)
            await set_tenant_context(db, str(tenant_id))
            result = await db.execute(
                select(BackgroundJob).where(BackgroundJob.id == uuid.UUID(job_id))
            )
            job = result.scalar_one_or_none()
            if job:
                job.complete()
                await db.commit()

        except Exception as e:
            logger.exception(f"Deep Work execution failed: {e}")
            # Use a fresh session for error handling (original may be in broken state)
            try:
                async with AsyncSessionLocal() as fail_db:
                    await set_tenant_context(fail_db, str(tenant_id))
                    result = await fail_db.execute(
                        select(BackgroundJob).where(BackgroundJob.id == uuid.UUID(job_id))
                    )
                    job = result.scalar_one_or_none()
                    if job:
                        job.fail(str(e))
                        await fail_db.commit()
            except Exception as inner_e:
                logger.error(f"Failed to mark Deep Work job {job_id} as failed: {inner_e}")
        finally:
            async with _executors_lock:
                _executors.pop(session_id, None)
            _sse_queues.pop(session_id, None)


# SSE queues per session
_sse_queues: dict[str, asyncio.Queue] = {}


# ---------------------------------------------------------------------------
# GET /deep-work/by-conversation/{conversation_id}/full-state
# ---------------------------------------------------------------------------

@router.get(
    "/deep-work/by-conversation/{conversation_id}/full-state",
    response_model=DeepWorkFullStateResponse,
)
async def get_full_state_by_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    Fetch full Deep Work state for a conversation. Returns the most recent
    session's state (latest wins). Frontend uses this to hydrate the store
    after navigation or refresh.
    """
    _check_feature_enabled()

    conv_uuid = _parse_uuid(conversation_id)
    if not conv_uuid:
        raise HTTPException(status_code=404, detail="No Deep Work session for this conversation")

    run = await dws.load_run(engine_dw.reading(db), tenant_id, conv_uuid)
    if run is not None:
        return await engine_dw.full_state(db, run, user_id)

    try:
        return await _build_full_state(db, conv_uuid, user_id, tenant_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("full-state endpoint failed for conversation %s: %s", conversation_id, exc)
        raise HTTPException(status_code=500, detail=f"Internal error: {exc}")


async def _build_full_state(
    db: AsyncSession, conv_uuid: UUID, user_id: UUID, tenant_id: UUID,
) -> DeepWorkFullStateResponse:
    result = await db.execute(
        select(BackgroundJob)
        .where(BackgroundJob.conversation_id == conv_uuid)
        .where(BackgroundJob.tenant_id == tenant_id)
        .where(BackgroundJob.user_id == user_id)
        .where(BackgroundJob.job_type == JobType.DEEP_WORK.value)
        .order_by(BackgroundJob.created_at.desc())
        .limit(1)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="No Deep Work session for this conversation")

    from app.services.session_storage import get_session_storage
    storage = get_session_storage()

    # Detect engine sessions from input_data
    is_engine = (
        isinstance(job.input_data, dict)
        and job.input_data.get("engine") == "orchestrator-worker"
    )

    # Read state.json from filesystem
    state_content = await storage.read(str(tenant_id), job.session_id, "state.json")
    state = json.loads(state_content) if state_content else {}

    # Read engine report and state for engine sessions
    engine_report = None
    engine_plan = None
    engine_state = None
    if is_engine:
        engine_report = await storage.read(str(tenant_id), job.session_id, "engine_report.md")
        plan_content = await storage.read(str(tenant_id), job.session_id, "engine_plan.json")
        if plan_content:
            try:
                engine_plan = json.loads(plan_content)
            except (json.JSONDecodeError, ValueError):
                pass
        # Read incremental engine state (sources + per-worker progress)
        engine_state_content = await storage.read(str(tenant_id), job.session_id, "engine_state.json")
        if engine_state_content:
            try:
                engine_state = json.loads(engine_state_content)
            except (json.JSONDecodeError, ValueError):
                pass

    # Read workspace files
    session_path = storage._get_session_path(str(tenant_id), job.session_id)
    workspace = session_path / "workspace"
    workspace_files = []
    if workspace.exists():
        for f in workspace.rglob("*"):
            if f.is_file() and not f.is_symlink():
                stat = f.stat()
                workspace_files.append(WorkspaceFileResponse(
                    name=f.name,
                    path=str(f.relative_to(workspace)),
                    size=stat.st_size,
                    type=f.suffix.lstrip(".") or "file",
                    modified_at=stat.st_mtime,
                ))

    # For engine sessions: derive status and todos from engine-specific data
    effective_status = state.get("status", job.status)
    todos: list[TodoSchema] = []

    if is_engine:
        # Detect orphaned engine jobs: DB says "running"/"pending" but no in-memory task
        if job.status in ("running", "pending") and job.session_id not in _engine_tasks:
            if engine_report:
                # Has report → completed successfully before server restart
                job.complete()
                effective_status = "completed"
            else:
                # No report → failed mid-execution
                job.fail("Server restarted during execution")
                effective_status = "failed"
            await db.commit()

        # Derive todos from engine_plan.json sub_queries with per-worker status
        if engine_plan and "sub_queries" in engine_plan:
            sub_queries = engine_plan["sub_queries"]
            is_done = effective_status in ("completed", "failed", "cancelled")
            is_running = effective_status == "running"

            # Build set of completed worker IDs from engine_state
            completed_worker_ids: set[str] = set()
            if engine_state and "completed_workers" in engine_state:
                for cw in engine_state["completed_workers"]:
                    if isinstance(cw, dict) and "worker_id" in cw:
                        completed_worker_ids.add(cw["worker_id"])

            plan_worker_ids: set[str] = set()
            for sq in sub_queries:
                text = sq.get("sub_query", sq) if isinstance(sq, dict) else str(sq)
                worker_id = sq.get("worker_id", "") if isinstance(sq, dict) else ""
                if worker_id:
                    plan_worker_ids.add(worker_id)
                if is_done:
                    todo_status = "completed"
                elif worker_id in completed_worker_ids:
                    todo_status = "completed"
                elif is_running:
                    todo_status = "in_progress"
                else:
                    todo_status = "pending"
                todos.append(TodoSchema(text=text, status=todo_status))

            # Backward-compat: append any completed workers NOT in the plan
            # (gap-filling tasks from older sessions that didn't re-save plan)
            if engine_state and "completed_workers" in engine_state:
                for cw in engine_state["completed_workers"]:
                    if isinstance(cw, dict) and cw.get("worker_id") not in plan_worker_ids:
                        todos.append(TodoSchema(
                            text=cw.get("sub_query", cw.get("worker_id", "Unknown task")),
                            status="completed",
                        ))
    else:
        todos = [TodoSchema(**t) for t in state.get("todos", [])]

    # For engine sessions, recover sources from engine_state.json
    if is_engine and engine_state and engine_state.get("sources"):
        sources = [SourceSchema(**s) for s in engine_state["sources"]]
    else:
        sources = [SourceSchema(**s) for s in state.get("sources", [])]

    # Derive engine phase from persisted state
    engine_phase = None
    if is_engine and engine_state:
        engine_phase = engine_state.get("phase")
    if effective_status in ("completed", "failed", "cancelled"):
        engine_phase = None

    # Propagate error message from orphan detection or background job
    error_message = None
    if effective_status == "failed":
        error_message = job.error or "Session failed unexpectedly"

    # Build workers list from engine_state for frontend worker card restoration
    workers_list = None
    if is_engine and engine_state and engine_state.get("completed_workers"):
        workers_list = engine_state["completed_workers"]

    # Extract persisted artifacts from engine_state
    artifacts_list = None
    if is_engine and engine_state and engine_state.get("artifacts"):
        artifacts_list = engine_state["artifacts"]

    return DeepWorkFullStateResponse(
        session_id=job.session_id,
        conversation_id=str(conv_uuid),
        status=effective_status,
        plan=engine_plan if is_engine else state.get("plan"),
        todos=todos,
        steps=state.get("steps", []),
        workspace_files=workspace_files,
        summary=engine_report if is_engine else state.get("summary"),
        key_findings=state.get("key_findings", []),
        sources=sources,
        connectors=[ConnectorSchema(**c) for c in state.get("connectors", [])],
        is_engine=is_engine,
        engine_report=engine_report,
        engine_phase=engine_phase,
        artifacts=artifacts_list,
        error=error_message,
        workers=workers_list,
    )


# ---------------------------------------------------------------------------
# GET /deep-work/{session_id}/events — SSE event stream
# ---------------------------------------------------------------------------

@router.get("/deep-work/{session_id}/events")
async def deep_work_events(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """
    SSE stream for a Deep Work session. Client connects and receives
    events as the AutonomousExecutor runs. Supports Last-Event-ID for
    reconnection.
    """
    _check_feature_enabled()

    # Verify ownership before streaming
    await _verify_session_ownership(db, session_id, tenant_id, user_id)

    queue = _sse_queues.get(session_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="Session not found or completed")

    async def stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield {
                        "event": event["type"],
                        "id": event.get("id", ""),
                        "data": json.dumps(event.get("data", {})),
                    }
                    # Check for terminal events
                    if event["type"] in ("deep_work_complete", "deep_work_error"):
                        break
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    yield {"comment": "keepalive"}
        except asyncio.CancelledError:
            pass

    return EventSourceResponse(
        stream(),
        media_type="text/event-stream",
        ping=15,
        send_timeout=30.0,
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# POST /deep-work/{session_id}/pause
# ---------------------------------------------------------------------------

@router.post("/deep-work/{session_id}/pause")
async def pause_deep_work(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Pause an active Deep Work session after the current step completes."""
    _check_feature_enabled()
    await _verify_session_ownership(db, session_id, tenant_id, user_id)

    executor = _executors.get(session_id)
    if executor is None:
        raise HTTPException(status_code=404, detail="No active session")

    await executor.pause(session_id)
    return {"status": "pausing"}


# ---------------------------------------------------------------------------
# POST /deep-work/{session_id}/resume
# ---------------------------------------------------------------------------

@router.post("/deep-work/{session_id}/resume")
async def resume_deep_work(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Resume a paused Deep Work session."""
    _check_feature_enabled()
    await _verify_session_ownership(db, session_id, tenant_id, user_id)

    executor = _executors.get(session_id)
    if executor is None:
        raise HTTPException(status_code=404, detail="No active session")

    await executor.resume(session_id)
    return {"status": "resuming"}


# ---------------------------------------------------------------------------
# POST /deep-work/{session_id}/stop
# ---------------------------------------------------------------------------

@router.post("/deep-work/{session_id}/stop")
async def stop_deep_work(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Stop a Deep Work session and finalize."""
    _check_feature_enabled()
    await _verify_session_ownership(db, session_id, tenant_id, user_id)

    executor = _executors.get(session_id)
    if executor is None:
        raise HTTPException(status_code=404, detail="No active session")

    await executor.stop(session_id)
    return {"status": "stopping"}


# ---------------------------------------------------------------------------
# POST /deep-work/{session_id}/input — respond to ask_user
# ---------------------------------------------------------------------------

@router.post("/deep-work/{session_id}/input")
async def send_human_input(
    session_id: str,
    body: HumanInputRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Send a human response to an ask_user prompt."""
    _check_feature_enabled()
    await _verify_session_ownership(db, session_id, tenant_id, user_id)

    executor = _executors.get(session_id)
    if executor is None:
        raise HTTPException(status_code=404, detail="No active session")

    await executor.send_human_input(session_id, body.input)
    return {"status": "input_received"}


# ---------------------------------------------------------------------------
# GET /deep-work/{session_id}/status
# ---------------------------------------------------------------------------

@router.get("/deep-work/{session_id}/status", response_model=SessionStatusResponse)
async def get_session_status(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Get current status and progress of a Deep Work session."""
    _check_feature_enabled()
    run = await engine_dw.find_engine_run(engine_dw.reading(db), tenant_id, session_id)
    if run is not None:
        return await engine_dw.session_status(db, run, user_id)

    from app.services.session_storage import get_session_storage
    storage = get_session_storage()

    state_content = await storage.read(str(tenant_id), session_id, "state.json")
    state = json.loads(state_content) if state_content else {}

    # Engine sessions don't write state.json — fall back to engine_state.json
    engine_state = None
    is_engine = False
    if not state:
        engine_state_content = await storage.read(str(tenant_id), session_id, "engine_state.json")
        if engine_state_content:
            try:
                engine_state = json.loads(engine_state_content)
                is_engine = True
            except (json.JSONDecodeError, ValueError):
                pass
        engine_plan_content = await storage.read(str(tenant_id), session_id, "engine_plan.json")
        if not engine_state and not engine_plan_content:
            raise HTTPException(status_code=404, detail="Session not found")

    import time
    elapsed = int(time.time() - state.get("created_at", time.time()))

    # Build sources from engine_state if available
    if engine_state and engine_state.get("sources"):
        sources = [SourceSchema(**s) for s in engine_state["sources"]]
    else:
        sources = [SourceSchema(**s) for s in state.get("sources", [])]

    # Derive effective status for engine sessions
    if is_engine:
        engine_phase = engine_state.get("phase", "running") if engine_state else "running"
        # If report exists, session completed even if phase wasn't updated
        if engine_phase != "completed":
            report_content = await storage.read(str(tenant_id), session_id, "engine_report.md")
            if report_content:
                engine_phase = "completed"
        # Cross-check DB for terminal states (failed/cancelled)
        if engine_phase not in ("completed", "failed", "cancelled"):
            result = await db.execute(
                select(BackgroundJob)
                .where(BackgroundJob.session_id == session_id)
                .where(BackgroundJob.tenant_id == tenant_id)
                .limit(1)
            )
            job = result.scalar_one_or_none()
            if job and job.status in ("failed", "cancelled", "completed"):
                engine_phase = job.status
        effective_status = engine_phase
    else:
        effective_status = state.get("status", "unknown")

    return SessionStatusResponse(
        session_id=session_id,
        status=effective_status,
        current_step=state.get("current_step", 0),
        total_steps=state.get("total_steps", 0),
        elapsed_seconds=elapsed,
        original_message=state.get("original_message"),
        plan=state.get("plan"),
        todos=[TodoSchema(**t) for t in state.get("todos", [])],
        sources=sources,
        connectors=[ConnectorSchema(**c) for c in state.get("connectors", [])],
        summary=state.get("summary"),
        key_findings=state.get("key_findings", []),
    )


# ---------------------------------------------------------------------------
# GET /deep-work/{session_id}/workspace — list files
# ---------------------------------------------------------------------------

@router.get("/deep-work/{session_id}/workspace")
async def list_workspace_files(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """List files in the session workspace."""
    _check_feature_enabled()
    await engine_dw.check_workspace_access(engine_dw.reading(db), tenant_id, user_id, session_id)

    from app.services.session_storage import get_session_storage
    storage = get_session_storage()

    session_path = storage._get_session_path(str(tenant_id), session_id)
    workspace = session_path / "workspace"

    files = []
    if workspace.exists():
        for f in workspace.rglob("*"):
            if f.is_file() and not f.is_symlink():
                stat = f.stat()
                files.append(WorkspaceFileResponse(
                    name=f.name,
                    path=str(f.relative_to(workspace)),
                    size=stat.st_size,
                    type=f.suffix.lstrip(".") or "file",
                    modified_at=stat.st_mtime,
                ))

    return {"files": [f.model_dump() for f in files]}


# ---------------------------------------------------------------------------
# GET /deep-work/{session_id}/workspace/{path:path} — download file
# ---------------------------------------------------------------------------

@router.get("/deep-work/{session_id}/workspace/{file_path:path}")
async def download_workspace_file(
    session_id: str,
    file_path: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Download a file from the session workspace."""
    _check_feature_enabled()
    await engine_dw.check_workspace_access(engine_dw.reading(db), tenant_id, user_id, session_id)

    from fastapi.responses import FileResponse
    from app.services.session_storage import get_session_storage

    storage = get_session_storage()
    session_path = storage._get_session_path(str(tenant_id), session_id)
    target = session_path / "workspace" / file_path

    # Prevent path traversal (Python 3.9+ is_relative_to is safe against partial-match attacks)
    try:
        resolved = target.resolve()
        workspace_resolved = (session_path / "workspace").resolve()
        if not resolved.is_relative_to(workspace_resolved):
            raise HTTPException(status_code=403, detail="Access denied")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid path")

    if not target.is_file() or target.is_symlink():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(str(target), filename=target.name)


# ---------------------------------------------------------------------------
# GET /deep-work/sessions — list user's Deep Work sessions
# ---------------------------------------------------------------------------

@router.get("/deep-work/sessions")
async def list_deep_work_sessions(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """List the current user's Deep Work sessions (most recent first)."""
    _check_feature_enabled()

    result = await db.execute(
        select(BackgroundJob)
        .where(BackgroundJob.tenant_id == tenant_id)
        .where(BackgroundJob.user_id == user_id)
        .where(BackgroundJob.job_type == JobType.DEEP_WORK.value)
        .order_by(BackgroundJob.created_at.desc())
        .limit(100)
    )
    jobs = result.scalars().all()

    sessions = []
    needs_commit = False
    for job in jobs:
        if isinstance(job.input_data, dict) and job.input_data.get("engine") == engine_dw.ENGINE_JOB_MARK:
            continue  # engine sessions are listed from deep_work_runs (reconciled), not as orphans
        plan_title = None
        effective_status = job.status
        if job.input_data and isinstance(job.input_data, dict):
            plan = job.input_data.get("plan", {})
            if isinstance(plan, dict):
                plan_title = plan.get("title")
            # Engine sessions: query field holds the title when plan.title missing
            if not plan_title and job.input_data.get("engine") == "orchestrator-worker":
                plan_title = job.input_data.get("query", "")[:80]

        # Fix orphaned running/pending jobs inline (no active in-memory task)
        if job.status in ("running", "pending") and job.session_id not in _engine_tasks:
            if job.session_id not in _executors:
                job.fail("Server restarted during execution")
                effective_status = "failed"
                needs_commit = True

        sessions.append(SessionListItem(
            session_id=job.session_id,
            conversation_id=str(job.conversation_id) if job.conversation_id else None,
            status=effective_status,
            plan_title=plan_title,
            created_at=job.created_at.isoformat() if job.created_at else None,
            updated_at=(job.completed_at or job.started_at or job.created_at).isoformat()
            if (job.completed_at or job.started_at or job.created_at) else None,
        ))

    if needs_commit:
        await db.commit()

    items = [s.model_dump() for s in sessions]
    if await engine_dw.has_engine_runs(db, tenant_id, user_id):
        items += await engine_dw.list_sessions(user_id, tenant_id)
        items.sort(key=lambda i: i.get("updated_at") or i.get("created_at") or "", reverse=True)
    return {"sessions": items}


# ---------------------------------------------------------------------------
# DELETE /deep-work/{session_id} — delete a session
# ---------------------------------------------------------------------------

@router.delete("/deep-work/{session_id}")
async def delete_deep_work_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Delete a Deep Work session: mark job as cancelled and remove filesystem data."""
    _check_feature_enabled()
    if await engine_dw.find_engine_run(engine_dw.reading(db), tenant_id, session_id) is not None:
        return await engine_dw.delete_session(session_id, db, user_id, tenant_id)

    job = await _verify_session_ownership(db, session_id, tenant_id, user_id)

    # Don't allow deleting a truly running session (with active task)
    if job.status == JobStatus.RUNNING.value:
        has_active_task = (
            session_id in _engine_tasks or session_id in _executors
        )
        if has_active_task:
            raise HTTPException(
                status_code=409,
                detail="Cannot delete a running session. Stop it first.",
        )

    # Mark as cancelled
    job.cancel()
    await db.commit()

    # Clean up filesystem
    try:
        from app.services.session_storage import get_session_storage
        import shutil
        storage = get_session_storage()
        session_path = storage._get_session_path(str(tenant_id), session_id)
        if session_path.exists():
            shutil.rmtree(session_path)
    except Exception as e:
        logger.warning(f"Failed to delete session filesystem for {session_id}: {e}")

    return {"status": "deleted"}


# ===========================================================================
# Deep Work Engine (v2) — Orchestrator-Worker-Synthesizer pipeline
# These endpoints run alongside the existing AutonomousExecutor endpoints.
# ===========================================================================


# ---------------------------------------------------------------------------
# POST /deep-work/engine/clarify — intake clarification
# ---------------------------------------------------------------------------

@router.post("/deep-work/engine/clarify")
async def clarify_deep_work_query(
    request: Request,
    body: ClarifyRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Ask the clarifier LLM whether to ask questions or auto-proceed."""
    _check_feature_enabled()
    if await engine_dw.tenant_uses_engine_deep_work(db, tenant_id, request):
        return await engine_dw.clarify(body, db, user_id, tenant_id)

    from app.services.deep_work.engine import DeepWorkEngine
    from app.services.deep_work.event_sink import NullSink
    from app.services.session_storage import get_session_storage

    agent_config = await _get_general_assistant_config(db, tenant_id)

    engine = DeepWorkEngine(
        db, get_session_storage(),
        str(tenant_id), str(user_id),
        str(uuid.uuid4()),
        agent_config=agent_config,
        event_sink=NullSink(),
    )

    result = await engine.generate_intake_questions(
        query=body.query,
        available_kbs=body.available_kbs,
        available_connectors=body.available_connectors,
    )
    return result.model_dump()


# ---------------------------------------------------------------------------
# POST /deep-work/engine/plan — plan-only (no execution)
# ---------------------------------------------------------------------------

@router.post("/deep-work/engine/plan", response_model=EnginePlanResponse)
async def plan_deep_work_engine(
    request: Request,
    body: DeepWorkEngineRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Run orchestration only and return a plan for user approval."""
    _check_feature_enabled()
    if await engine_dw.tenant_uses_engine_deep_work(db, tenant_id, request):
        return await engine_dw.plan(body, db, user_id, tenant_id)

    from app.services.deep_work.engine import DeepWorkEngine
    from app.services.deep_work.event_sink import NullSink
    from app.services.session_storage import get_session_storage

    agent_config = await _get_general_assistant_config(db, tenant_id)

    engine = DeepWorkEngine(
        db, get_session_storage(),
        str(tenant_id), str(user_id),
        body.session_id or str(uuid.uuid4()),
        agent_config=agent_config,
        event_sink=NullSink(),
    )

    plan = await engine.plan_only(body.query, enriched_query=body.enriched_query)
    steps = [t.sub_query for t in plan.sub_queries]
    title = plan.title or body.query[:80]

    return EnginePlanResponse(
        title=title,
        steps=steps,
        raw_plan=plan.model_dump(),
    )


# ---------------------------------------------------------------------------
# POST /deep-work/engine — start orchestrator-worker engine
# ---------------------------------------------------------------------------

@router.post("/deep-work/engine")
async def start_deep_work_engine(
    request: Request,
    body: DeepWorkEngineRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Start a deep work engine session (v2 orchestrator-worker pipeline)."""
    _check_feature_enabled()
    if await engine_dw.tenant_uses_engine_deep_work(db, tenant_id, request):
        return await engine_dw.start(body, db, user_id, tenant_id)

    session_id = body.session_id or str(uuid.uuid4())

    # Guard against duplicate session starts
    if session_id in _engine_tasks:
        raise HTTPException(status_code=409, detail="Session already active")

    sse_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _sse_queues[session_id] = sse_queue

    # Load agent config for model tier resolution (using request-scoped db)
    agent_config = await _get_general_assistant_config(db, tenant_id)

    # Create BackgroundJob record — matches AutonomousExecutor pattern
    job = BackgroundJob(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        job_type=JobType.DEEP_WORK.value,
        status=JobStatus.RUNNING.value,
        trigger=JobTrigger.MANUAL.value,
        session_id=session_id,
        conversation_id=_parse_uuid(body.conversation_id),
        input_data={
            "query": body.query,
            "engine": "orchestrator-worker",
        },
    )
    db.add(job)
    await db.commit()

    async def _run_engine():
        """Background task with its own DB session."""
        from app.services.deep_work.engine import DeepWorkEngine
        from app.services.deep_work.event_sink import SSEQueueSink
        from app.services.session_storage import get_session_storage

        async with AsyncSessionLocal() as bg_db:
            await set_tenant_context(bg_db, str(tenant_id))
            try:
                engine = DeepWorkEngine(
                    bg_db, get_session_storage(),
                    str(tenant_id), str(user_id), session_id,
                    agent_config=agent_config,
                    event_sink=SSEQueueSink(sse_queue),
                )
                # Reconstruct precomputed plan if provided
                precomputed_plan = None
                if body.plan:
                    from app.services.deep_work.models import DeepWorkPlan
                    precomputed_plan = DeepWorkPlan.model_validate(body.plan)
                await engine.run(
                    body.query,
                    precomputed_plan=precomputed_plan,
                    enriched_query=body.enriched_query,
                )
                await set_tenant_context(bg_db, str(tenant_id))
                await _update_job_status(bg_db, session_id, JobStatus.COMPLETED)
            except asyncio.CancelledError:
                logger.info("Deep work engine cancelled: %s", session_id)
                await set_tenant_context(bg_db, str(tenant_id))
                await _update_job_status(bg_db, session_id, JobStatus.CANCELLED)
            except Exception as exc:
                logger.exception("Deep work engine failed: %s", exc)
                await set_tenant_context(bg_db, str(tenant_id))
                await _update_job_status(bg_db, session_id, JobStatus.FAILED, str(exc))
                await sse_queue.put({
                    "id": str(uuid.uuid4()),
                    "type": "deep_work_engine_error",
                    "data": {"message": str(exc)},
                })
            finally:
                _engine_tasks.pop(session_id, None)
                _sse_queues.pop(session_id, None)

    task = asyncio.create_task(_run_engine())
    _engine_tasks[session_id] = task

    return {"session_id": session_id, "status": "started"}


# ---------------------------------------------------------------------------
# GET /deep-work/engine/{session_id}/stream — SSE event stream
# ---------------------------------------------------------------------------

@router.get("/deep-work/engine/{session_id}/stream")
async def stream_deep_work_engine(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """SSE stream for a deep work engine session."""
    _check_feature_enabled()
    await _verify_session_ownership(db, session_id, tenant_id, user_id)

    queue = _sse_queues.get(session_id)
    if not queue:
        raise HTTPException(status_code=404, detail="Engine session not found or completed")

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=60.0)
                    yield {
                        "id": event["id"],
                        "event": event["type"],
                        "data": json.dumps(event["data"]),
                    }
                    if event["type"] in (
                        "deep_work_engine_complete",
                        "deep_work_engine_error",
                    ):
                        break
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}
        except asyncio.CancelledError:
            pass

    return EventSourceResponse(
        event_generator(),
        media_type="text/event-stream",
        ping=15,
        send_timeout=60.0,
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# POST /deep-work/engine/{session_id}/cancel — cancel engine task
# ---------------------------------------------------------------------------

@router.post("/deep-work/engine/{session_id}/cancel")
async def cancel_deep_work_engine(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    tenant_id: UUID = Depends(get_current_tenant_id),
):
    """Cancel a running deep work engine task (an engine-path session cancels through the engine)."""
    _check_feature_enabled()
    if await engine_dw.find_engine_run(engine_dw.reading(db), tenant_id, session_id) is not None:
        return await engine_dw.cancel(session_id, db, user_id, tenant_id)
    await _verify_session_ownership(db, session_id, tenant_id, user_id)

    task = _engine_tasks.get(session_id)
    if not task or task.done():
        raise HTTPException(status_code=404, detail="No active engine session")

    task.cancel()

    queue = _sse_queues.get(session_id)
    if queue:
        await queue.put({
            "id": str(uuid.uuid4()),
            "type": "deep_work_engine_error",
            "data": {"message": "Cancelled by user"},
        })

    return {"session_id": session_id, "status": "cancelled"}


# Engine-only routes (steer, worker drill-down watch); each checks ownership / the tenant gate itself.
router.include_router(engine_dw.router)
