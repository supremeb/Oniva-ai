"""Start and cancel workflow runs on the engine (fleet-workflows-on-engine plan, U15).

A workflow run is an ``engine_conversations`` row bound to the execution (``run_kind = "workflow"``),
one ``workflow_run`` host task started by ``POST /v1/runs`` and nothing in the API process. The ids
are deterministic: the conversation id is ``uuid5(RUN_NAMESPACE, "workflow:{execution_id}")`` and the
start's ``Idempotency-Key`` is ``workflow:{execution_id}``, so a retried start (client, reconciler)
never doubles anything. A client ``Idempotency-Key`` also makes the execution id stable.

The run executes the spec it started with: ``start_workflow_run`` freezes it into the execution's
``input_data["__spec"]`` (the node gateway reads it), so an edit mid-run changes nothing.

Cancel is a real engine ``abort`` of the container conversation. An abort that cannot reach the engine
still cancels the row and sets ``input_data["abort_pending"]``; ``find_stuck`` returns such rows and
``start_missing`` retries the abort.

Registers the ``workflow`` ``start_missing``, ``find_stuck`` and ``fail`` hooks (``on_finished`` is ``workflow_run_sync``'s, U16).
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Callable, Dict, Optional

from fastapi import HTTPException
from sqlalchemy import String, cast, select

from app.core.database import get_db_for_tenant
from app.models.engine import EngineConversation
from app.models.tenant import Tenant
from app.models.workflow import WorkflowExecution
from app.services import run_handlers
from app.services.automation_scope import RUN_NAMESPACE, bind_run_conversation, run_conversation_id
from app.services.engine_client import EngineError, get_engine_client, run_start_body
from app.services.feature_flags import engine_workflows_enabled
from app.services.workflow_compiler import build_run_input, compile_for_run

logger = logging.getLogger(__name__)

KIND = "workflow"
TASK_KIND = "workflow_run"
SPEC_KEY = "__spec"
STUCK_BATCH = 200
TERMINAL = ("completed", "failed", "cancelled")
#: A sync run waits this long for the engine, polling every POLL_INTERVAL_S (module constants: tests shrink them).
SYNC_WAIT_S = 120.0
POLL_INTERVAL_S = 0.5


def _uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def request_id(execution_id: Any) -> str:
    return f"workflow:{execution_id}"


def execution_id_for(tenant_id: Any, workflow_id: Any, request_key: Optional[str]) -> uuid.UUID:
    """Stable per ``(tenant, workflow, client Idempotency-Key)``; a fresh id when there is no key."""
    if not request_key:
        return uuid.uuid4()
    return uuid.uuid5(RUN_NAMESPACE, f"execution:{tenant_id}:{workflow_id or 'adhoc'}:{request_key}")


def tenant_factory(tenant_id: Any) -> Callable:
    """Own tenant-scoped session per call; tests replace this with their database."""
    return functools.partial(get_db_for_tenant, str(tenant_id))


async def tenant_uses_engine_workflows(db, tenant_id: Any) -> bool:
    """True when this tenant's workflow runs start on the engine. Any failure reads as off."""
    try:
        tenant = await db.get(Tenant, _uuid(tenant_id))
        return tenant is not None and engine_workflows_enabled(tenant) is True
    except Exception:
        logger.warning("engine workflows flag lookup failed for tenant %s", tenant_id, exc_info=True)
        return False


def is_engine_execution(execution: Any) -> bool:
    return getattr(execution, "engine_conversation_id", None) is not None


def settle(execution: WorkflowExecution, status: str, *, error: Optional[str] = None) -> bool:
    """Move a non-terminal execution to a terminal ``status``. A terminal row never moves (False)."""
    if execution.status in TERMINAL:
        return False
    execution.status = status
    execution.completed_at = datetime.now(timezone.utc)
    if error is not None:
        execution.error = error[:2000]
    return True


def _failure_code(exc: EngineError) -> str:
    return "engine_unreachable" if exc.status in (502, 504) else f"engine_rejected:{exc.error}"


def _agent_tag(execution_id: Any) -> dict:
    # The worker's run-finished push fires only for conversations tagged notify=backend (U5).
    return {"metadata": {"notify": "backend", "runKind": "workflow", "executionId": str(execution_id)}}


async def _compile(db, tenant_id: Any, spec: dict, workflow_id: Any) -> dict:
    try:
        return await compile_for_run(db, _uuid(tenant_id), spec, workflow_id=workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


async def start_engine_run(factory: Callable, execution_id: Any, *, engine: Any = None,
                           deadline_ms: Optional[int] = None, recover: bool = False) -> run_handlers.StartResult:
    """Start (or idempotently re-start) the engine run of a ``queued`` execution. Never raises for engine trouble.

    ``recover`` also accepts a ``running`` execution and only asks the engine for its task id (the start is
    idempotent: the engine answers the existing run), without touching the row.
    """
    execution_id = _uuid(execution_id)
    async with factory() as db:
        execution = await db.get(WorkflowExecution, execution_id)
        if execution is None:
            return run_handlers.StartResult.failed("execution_not_found")
        if execution.status != "queued" and not (recover and execution.status == "running"):
            return run_handlers.StartResult.already_started()
        tenant_id, user_id, workflow_id = execution.tenant_id, execution.user_id, execution.workflow_id
        spec = (execution.input_data or {}).get(SPEC_KEY)
        if not isinstance(spec, dict):
            return run_handlers.StartResult.failed("spec_missing")
        try:
            compiled = await compile_for_run(db, tenant_id, spec, workflow_id=workflow_id)
        except ValueError as exc:
            return run_handlers.StartResult.failed(f"invalid_workflow: {exc}")

    engine = engine or get_engine_client()
    key = request_id(execution_id)
    try:
        conversation_id, project_id, session_id = await bind_run_conversation(
            factory, KIND, execution_id, tenant_id, user_id)
        body = run_start_body(TASK_KIND, conversation_id, build_run_input(execution_id, compiled, deadline_ms=deadline_ms),
                              agent=_agent_tag(execution_id))
        started = await engine.start_run(session_id, tenant_id, project_id, body, key)
    except (EngineError, OSError, TimeoutError) as exc:
        code = _failure_code(exc) if isinstance(exc, EngineError) else "engine_unreachable"
        logger.warning("workflow execution %s could not start on the engine: %s", execution_id, exc)
        return run_handlers.StartResult.failed(code)

    async with factory() as db:
        execution = await db.get(WorkflowExecution, execution_id)
        if execution is not None and execution.status == "queued" and not recover:
            execution.status = "running"
            execution.engine_conversation_id = conversation_id
            execution.error = None
    task_id = started.get("taskId") if isinstance(started, dict) else None
    return run_handlers.StartResult.started(task_id)


async def start_workflow_run(
    db,
    workflow: Any,
    spec: dict,
    input: Optional[dict],
    *,
    user_id: Any,
    tenant_id: Any,
    trigger: str,
    request_key: Optional[str] = None,
    version_id: Any = None,
    factory: Optional[Callable] = None,
    engine: Any = None,
    deadline_ms: Optional[int] = None,
) -> tuple[WorkflowExecution, Optional[str]]:
    """Create the execution (``queued``), bind its conversation and start the engine run.

    Returns ``(execution, task_id)``; ``task_id`` is None for a replay of an already-settled start.
    Raises ``HTTPException`` 400 for an invalid workflow and 502 when the engine cannot start it; in that
    case the execution is committed ``failed`` with the code (never a dangling ``queued``).
    """
    tenant_id, user_id = _uuid(tenant_id), _uuid(user_id)
    workflow_id = getattr(workflow, "id", workflow)  # a workflow row, its id, or None (ad-hoc spec)
    await _compile(db, tenant_id, spec, workflow_id)  # refuse an invalid workflow before any row exists

    execution_id = execution_id_for(tenant_id, workflow_id, request_key)
    existing = (await db.execute(
        select(WorkflowExecution).where(WorkflowExecution.id == execution_id,
                                        WorkflowExecution.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if existing is None:
        variables: Dict[str, Any] = dict(input or {})
        variables[SPEC_KEY] = spec
        if version_id is not None:
            variables["workflow_version_id"] = str(version_id)
        existing = WorkflowExecution(
            id=execution_id, workflow_id=workflow_id, user_id=user_id, tenant_id=tenant_id,
            engine_conversation_id=run_conversation_id(KIND, execution_id),
            status="queued", trigger=trigger, input_data=variables, results={}, execution_trace=[],
            total_nodes=len(spec.get("nodes") or []), started_at=datetime.now(timezone.utc),
        )
        db.add(existing)
        await db.commit()
    await db.refresh(existing)
    if existing.status == "running":
        # A replay of a start that went through: the engine answers the existing run with its task id.
        recovered = await start_engine_run(factory or tenant_factory(tenant_id), execution_id,
                                           engine=engine, recover=True)
        return existing, recovered.task_id
    if existing.status != "queued":
        return existing, None  # settled

    result = await start_engine_run(factory or tenant_factory(tenant_id), execution_id,
                                    engine=engine, deadline_ms=deadline_ms)
    if result.status == "failed":
        error = result.error or "engine_unreachable"
        await db.refresh(existing)
        if existing.status == "queued":
            settle(existing, "failed", error=error)
            await db.commit()
        raise HTTPException(status_code=502, detail={"error": error, "execution_id": str(execution_id)})
    await db.refresh(existing)
    return existing, result.task_id


async def _binding(db, execution: WorkflowExecution) -> Optional[tuple[str, Any]]:
    if execution.engine_conversation_id is None:
        return None
    row = (await db.execute(
        select(EngineConversation.session_id, EngineConversation.project_id)
        .where(EngineConversation.conversation_id == execution.engine_conversation_id)
    )).first()
    return (row.session_id, row.project_id) if row is not None else None


async def abort_run(db, execution: WorkflowExecution, reason: Optional[str] = None, *, engine: Any = None) -> bool:
    """Abort the execution's engine run. True when settled (aborted, or nothing to abort); False on an outage.

    A 404/409 from the engine means the run is gone or already settled, which is the goal state.
    """
    bound = await _binding(db, execution)
    if bound is None:
        return True
    session_id, project_id = bound
    try:
        await (engine or get_engine_client()).abort(
            session_id, execution.tenant_id, project_id, execution.engine_conversation_id, reason)
    except EngineError as exc:
        if exc.status in (404, 409):
            return True
        logger.warning("workflow execution %s: engine abort failed: %s", execution.id, exc)
        return False
    except (OSError, TimeoutError) as exc:
        logger.warning("workflow execution %s: engine abort failed: %s", execution.id, exc)
        return False
    return True


def set_abort_pending(execution: WorkflowExecution, pending: bool) -> None:
    data = dict(execution.input_data or {})
    if pending:
        data["abort_pending"] = True
    else:
        data.pop("abort_pending", None)
    execution.input_data = data


async def cancel_workflow_run(db, execution: WorkflowExecution, reason: Optional[str] = None, *,
                              engine: Any = None) -> bool:
    """Abort the engine run, then mark the row ``cancelled`` (KTD9). The caller commits.

    Returns False when the row is already terminal (nothing is done). An engine outage still cancels the
    row and leaves the abort to the reconciler.
    """
    if execution.status in TERMINAL:
        return False
    settled = await abort_run(db, execution, reason, engine=engine)
    set_abort_pending(execution, not settled)
    settle(execution, "cancelled", error=reason or "Execution cancelled by user")
    return True


# ---- waiting for a sync run ---------------------------------------------------------------------


async def await_finished(db, execution: WorkflowExecution, task_id: Optional[str], *, engine: Any = None,
                         wait_s: Optional[float] = None, poll_s: Optional[float] = None) -> Optional[dict]:
    """Poll the run task until it is terminal. Returns its outcome, or None if still going after ``wait_s``."""
    if task_id is None:
        return None
    bound = await _binding(db, execution)
    if bound is None:
        return None
    session_id, project_id = bound
    engine = engine or get_engine_client()
    deadline = time.monotonic() + (SYNC_WAIT_S if wait_s is None else wait_s)
    interval = POLL_INTERVAL_S if poll_s is None else poll_s
    while True:
        try:
            task = await engine.get_task(session_id, execution.tenant_id, project_id,
                                         execution.engine_conversation_id, task_id)
        except EngineError as exc:
            if exc.status not in (502, 504):
                raise
            task = {}
        if task.get("status") == "terminal":
            return task.get("outcome") or {}
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(interval)


def outcome_status(outcome: Optional[dict]) -> str:
    """Execution status for a terminal run task outcome."""
    kind = (outcome or {}).get("type")
    if kind == "done":
        value = outcome.get("value") or {}
        status = value.get("status") if isinstance(value, dict) else None
        return status if status in TERMINAL else "failed"
    return "cancelled" if kind == "aborted" else "failed"


def results_from_trace(execution: WorkflowExecution) -> Dict[str, Any]:
    """``{node_id: {status, output, error}}`` from the stored trace (the last step per node wins)."""
    results: Dict[str, Any] = {}
    for step in execution.execution_trace or []:
        results[step["node_id"]] = {"status": step.get("status"), "output": step.get("output"), "error": step.get("error")}
    return results


def executed_count(results: Dict[str, Any]) -> int:
    return sum(1 for r in results.values() if r.get("status") == "success")


async def execution_events(factory: Callable, execution_id: Any, task_id: Optional[str], *, engine: Any = None,
                           timeout_s: float = 300.0, poll_s: Optional[float] = None) -> AsyncIterator[dict]:
    """Orchestrator-shaped events (``progress``, ``node_complete``, ``complete``) for an engine run.

    Built from the stored trace and the run task, polled until the task is terminal. This is the
    interim live view for the agent-chat and compliance streams; U17 replaces it with the engine watch.
    Raises ``asyncio.TimeoutError`` when the run is still going after ``timeout_s``.
    """
    execution_id = _uuid(execution_id)
    seen: set = set()
    deadline = time.monotonic() + timeout_s
    interval = POLL_INTERVAL_S if poll_s is None else poll_s
    while True:
        async with factory() as db:
            execution = await db.get(WorkflowExecution, execution_id)
            if execution is None:
                return
            outcome = await await_finished(db, execution, task_id, engine=engine, wait_s=0, poll_s=0)
            # Read the trace after the task state: every step is written before the run settles.
            await db.refresh(execution)
            steps = [dict(step) for step in execution.execution_trace or []]
            total = execution.total_nodes or 0
        for step in steps:
            ident = (step.get("node_id"), step.get("run_attempt"), step.get("attempt"))
            if ident in seen:
                continue
            seen.add(ident)
            yield {"type": "progress", "node_id": step["node_id"]}
            yield {"type": "node_complete", "node_id": step["node_id"],
                   "result": {"status": step.get("status"), "output": step.get("output"), "error": step.get("error")}}
        if outcome is not None:
            results: Dict[str, Any] = {}
            for step in steps:
                results[step["node_id"]] = {"status": step.get("status"), "output": step.get("output"),
                                            "error": step.get("error")}
            yield {"type": "complete", "status": outcome_status(outcome), "results": results,
                   "executed_nodes": executed_count(results), "total_nodes": total,
                   "execution_id": str(execution_id)}
            return
        if time.monotonic() >= deadline:
            raise asyncio.TimeoutError()
        await asyncio.sleep(interval)


# ---- registry hooks -----------------------------------------------------------------------------


def _ref(execution: WorkflowExecution) -> run_handlers.RunRef:
    extra = {"abort_pending": True} if (execution.input_data or {}).get("abort_pending") else {}
    return run_handlers.RunRef(kind=KIND, ref_id=execution.id, tenant_id=execution.tenant_id,
                               conversation_id=execution.engine_conversation_id, extra=extra)


async def find_stuck(session, older_than: timedelta) -> list:
    """Engine executions quiet long enough to look at: never started (``queued``), still ``running``
    (a settle whose push was lost), and cancelled rows whose engine abort never got through."""
    cutoff = datetime.now(timezone.utc) - older_than
    rows = (await session.execute(
        select(WorkflowExecution).where(
            WorkflowExecution.status.in_(("queued", "running")),
            WorkflowExecution.engine_conversation_id.is_not(None),
            WorkflowExecution.started_at < cutoff,
        ).order_by(WorkflowExecution.started_at).limit(STUCK_BATCH)
    )).scalars().all()
    aborting = (await session.execute(
        select(WorkflowExecution).where(
            WorkflowExecution.status == "cancelled",
            WorkflowExecution.engine_conversation_id.is_not(None),
            WorkflowExecution.completed_at < cutoff,
            cast(WorkflowExecution.input_data, String).like("%abort_pending%"),
        ).order_by(WorkflowExecution.completed_at).limit(STUCK_BATCH)
    )).scalars().all()
    return ([_ref(r) for r in rows]
            + [_ref(r) for r in aborting if (r.input_data or {}).get("abort_pending")])


async def _retry_abort(ref: run_handlers.RunRef) -> run_handlers.StartResult:
    async with tenant_factory(ref.tenant_id)() as db:
        execution = await db.get(WorkflowExecution, ref.ref_id)
        if execution is None or not (execution.input_data or {}).get("abort_pending"):
            return run_handlers.StartResult.already_started()
        if not await abort_run(db, execution, execution.error):
            return run_handlers.StartResult.failed("abort_unavailable")
        set_abort_pending(execution, False)
    return run_handlers.StartResult.already_started()


async def start_missing(ref: run_handlers.RunRef) -> run_handlers.StartResult:
    if ref.tenant_id is None:
        return run_handlers.StartResult.failed("tenant_unknown")
    async with tenant_factory(ref.tenant_id)() as db:
        execution = await db.get(WorkflowExecution, ref.ref_id)
        status = None if execution is None else execution.status
        retry_abort = (status == "cancelled" and bool((execution.input_data or {}).get("abort_pending")))
    if retry_abort:
        return await _retry_abort(ref)
    # A queued row was never started. A running row whose engine run is gone (the reconciler only calls this
    # then) is started again under the same keys: stored node outcomes are replayed, nothing runs twice.
    return await start_engine_run(tenant_factory(ref.tenant_id), ref.ref_id, recover=(status == "running"))


async def fail(ref: run_handlers.RunRef, code: str) -> None:
    if ref.tenant_id is None:
        return
    async with tenant_factory(ref.tenant_id)() as db:
        execution = await db.get(WorkflowExecution, ref.ref_id)
        if execution is not None:
            settle(execution, "failed", error=code)


def register_hooks() -> None:
    run_handlers.register(KIND, start_missing=start_missing, find_stuck=find_stuck, fail=fail)


register_hooks()
