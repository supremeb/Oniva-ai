"""Workflow read model (fleet-workflows-on-engine plan, U16): ``finalize`` and the ``workflow`` ``on_finished`` hook.

``finalize`` is what the ``workflow_run`` task posts when the run ends (``/internal/workflows/runs/{id}/finalize``);
``on_finished`` is the registry hook for ``POST /internal/runs/finished`` and the reconciler, which settles an
execution whose run ended without a finalize (aborted, faulted, or the post was lost). Both go through
``apply_final``. Rules:

* A terminal row never moves: ``cancelled`` stays cancelled, a repeated finalize changes nothing.
* ``total_tokens`` is the sum of the ledger rows of the run's conversation (the trace's own count is the fallback).
* Skipped nodes are recorded in ``results`` and are not counted in ``nodes_executed``.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import func, select

from app.models.llm_usage import LLMUsageRecord
from app.models.scheduled_workflow import ScheduledWorkflow, ScheduledWorkflowExecution
from app.models.workflow import WorkflowExecution
from app.services import run_handlers
from app.services import workflow_engine_runs as wer
from app.services.deep_work.engine_view import read_snapshot
from app.services.engine_client import get_engine_client
from app.services.run_status import settled_status
from app.services.webhook_service import WebhookConfig, get_webhook_service
from app.services.workflow_stream_adapter import node_states, run_ended, run_task

logger = logging.getLogger(__name__)

KIND = wer.KIND
TERMINAL = wer.TERMINAL
_DEFAULT_ERROR = "The workflow run failed."


async def ledger_tokens(db, execution: WorkflowExecution) -> Optional[int]:
    """Input plus output tokens of the ledger rows of the run's conversation; None when there are none."""
    if execution.engine_conversation_id is None:
        return None
    total, rows = (await db.execute(
        select(func.coalesce(func.sum(LLMUsageRecord.input_tokens + LLMUsageRecord.output_tokens), 0),
               func.count(LLMUsageRecord.id)).where(
            LLMUsageRecord.tenant_id == execution.tenant_id,
            LLMUsageRecord.conversation_id == execution.engine_conversation_id)
    )).one()
    return int(total) if rows else None


def _error_text(error: Any) -> Optional[str]:
    if error is None:
        return None
    if isinstance(error, dict):
        message = error.get("message")
        if message:
            return str(message)[:2000]
    return str(error)[:2000]


def _summaries_from_trace(execution: WorkflowExecution) -> Dict[str, str]:
    summaries: Dict[str, str] = {}
    for step in execution.execution_trace or []:
        summaries[step["node_id"]] = step.get("status") or "failed"
    return summaries


def _status_from_summaries(summaries: Dict[str, str]) -> str:
    return "failed" if any(s in ("failed", "blocked") for s in summaries.values()) else "completed"


async def finish_scheduled(db, execution: WorkflowExecution, status: str, *, attempts: Optional[int] = None) -> None:
    """The scheduled-run row of this execution ends with it, and the schedule's ``last_run_*`` follow (U19).

    ``attempts`` is the run's final attempt count (finalize); ``retry_count`` is the retries it took. Without it
    (settled by the reconciler) the stored ``retry_count`` stays.
    """
    if execution.trigger != "scheduled":
        return
    rows = (await db.execute(
        select(ScheduledWorkflowExecution).where(ScheduledWorkflowExecution.workflow_execution_id == execution.id)
    )).scalars().all()
    if not rows:  # linked by the start; fall back to the deterministic request key
        rows = (await db.execute(
            select(ScheduledWorkflowExecution).where(ScheduledWorkflowExecution.tenant_id == execution.tenant_id,
                                                     ScheduledWorkflowExecution.workflow_execution_id.is_(None),
                                                     ScheduledWorkflowExecution.status.in_(("pending", "running")))
        )).scalars().all()
        rows = [r for r in rows if scheduled_execution_key_matches(r, execution)]
    for row in rows:
        if row.status in ("completed", "failed", "skipped"):
            continue
        final = "completed" if status == "completed" else "failed"
        error = execution.error
        if attempts is not None:
            row.retry_count = max(0, attempts - 1)
            if final == "failed" and attempts > 1 and error:
                error = f"{error} (failed after {attempts} attempts)"[:2000]
        row.status = final
        row.completed_at = execution.completed_at
        row.error = error
        summary = dict(row.result_summary or {})
        summary.update({"status": status, "nodes_executed": execution.nodes_executed,
                        "total_tokens": execution.total_tokens})
        if attempts is not None:
            summary["attempts"] = attempts
        row.result_summary = summary
        schedule = await db.get(ScheduledWorkflow, row.schedule_id)
        if schedule is not None:
            schedule.last_run_at = execution.completed_at
            schedule.last_run_status = final
            schedule.last_error = error if final == "failed" else None


def scheduled_execution_key_matches(row: ScheduledWorkflowExecution, execution: WorkflowExecution) -> bool:
    """True when ``execution`` is the engine run of scheduled execution ``row`` (deterministic request key)."""
    return wer.execution_id_for(row.tenant_id, execution.workflow_id, f"scheduled:{row.id}") == execution.id


async def apply_final(db, execution: WorkflowExecution, status: str, *, summaries: Optional[Dict[str, str]] = None,
                      error: Any = None, attempts: Optional[int] = None) -> bool:
    """Settle ``execution`` (locked by the caller). False when it was already terminal (nothing changes)."""
    if execution.status in TERMINAL:
        return False
    summaries = dict(summaries) if summaries is not None else _summaries_from_trace(execution)
    results = wer.results_from_trace(execution)
    for node_id, node_status in summaries.items():
        results.setdefault(node_id, {"status": node_status, "output": None, "error": None})
    message = _error_text(error)
    if status == "failed" and message is None:
        message = next((r.get("error") for r in results.values() if r.get("status") == "failed" and r.get("error")),
                       None) or _DEFAULT_ERROR
    now = datetime.now(timezone.utc)
    execution.status = status
    execution.results = results
    execution.error = message
    execution.nodes_executed = sum(1 for s in summaries.values() if s in ("success", "failed"))
    ledger = await ledger_tokens(db, execution)
    if ledger is not None:
        execution.total_tokens = ledger
    execution.completed_at = now
    started = execution.started_at
    if started is not None:
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        execution.duration_ms = max(0, int((now - started).total_seconds() * 1000))
    await finish_scheduled(db, execution, status, attempts=attempts)
    await wer.close_wait_approvals(db, execution, f"run {status}")  # no node is waiting on a human any more
    return True


def completion_callback(execution: WorkflowExecution) -> Optional[Dict[str, Any]]:
    """The run's completion callback (``request.webhook``) as send arguments, or None when it has none.

    Built by the call that moved the row, before it commits, and sent with ``send_completion_webhook`` after the
    commit: a slow receiver never holds the row lock, and a rolled-back settle sends nothing.
    """
    hook = wer.completion_webhook(execution)
    if hook is None:
        return None
    total = execution.total_nodes or 0
    return {
        "config": WebhookConfig(url=hook["url"], secret=hook.get("secret"), headers=hook.get("headers") or {}),
        "execution_id": str(execution.id), "tenant_id": str(execution.tenant_id),
        "workflow_id": str(execution.workflow_id) if execution.workflow_id else None,
        # The webhook service names the event from "success"; engine statuses say "completed".
        "status": "success" if execution.status == "completed" else execution.status,
        "results": execution.results or {}, "executed_nodes": execution.nodes_executed or 0, "total_nodes": total,
        "execution_time_ms": execution.duration_ms,
        "message": execution.error or f"Workflow executed: {execution.nodes_executed or 0}/{total} nodes",
    }


async def send_completion_webhook(callback: Optional[Dict[str, Any]]) -> None:
    """Send a ``completion_callback`` once the settle has committed. Never raises."""
    if callback is None:
        return
    try:
        await get_webhook_service().send_completion_webhook(**callback)
    except Exception:
        logger.exception("workflow execution %s: completion webhook failed", callback["execution_id"])


async def finalize(factory, tenant_id: Any, run_id: Any, body: Any) -> bool:
    """Apply a validated ``finalize.input`` (``body``) to the run's execution. True when it moved the row."""
    run_id = wer._uuid(run_id)
    async with factory(str(tenant_id)) as db:
        execution = (await db.execute(
            select(WorkflowExecution).where(WorkflowExecution.id == run_id,
                                            WorkflowExecution.tenant_id == wer._uuid(tenant_id)).with_for_update()
        )).scalar_one_or_none()
        if execution is None:
            return False
        moved = await apply_final(db, execution, body.status, summaries=dict(body.nodeSummaries), error=body.error,
                                  attempts=body.attempts)
        is_batch = execution.trigger == "batch"
        callback = completion_callback(execution) if moved else None
        await db.commit()
    await send_completion_webhook(callback)
    if moved and is_batch:
        # A batch settles with its run, not on the reconciler's next pass.
        from app.services import workflow_batch_engine  # lazy: it imports this module's neighbours
        await workflow_batch_engine.on_finished(
            run_handlers.RunRef(kind=workflow_batch_engine.KIND, ref_id=run_id, tenant_id=wer._uuid(tenant_id)),
            {"runStatus": body.status})
    return moved


def _outcome(snapshot: dict) -> Optional[str]:
    """The run status a settled snapshot stands for; None while the engine still has work or the status is unknown."""
    return settled_status(snapshot)


async def _view_of(ref: run_handlers.RunRef, snapshot: dict) -> Optional[dict]:
    """The engine view of the run's conversation: the snapshot's own, else read through the view path.

    None when the ref carries no binding (nothing to read). An ``EngineError`` propagates, so a push whose view
    cannot be read now is retried by the engine's outbox rather than judged by the trace alone.
    """
    if snapshot.get("view") is not None:
        return snapshot["view"]
    if ref.tenant_id is None or ref.project_id is None or ref.conversation_id is None:
        return None
    engine = get_engine_client()
    session_id = ref.session_id or engine.directory.default_session_id
    return await read_snapshot(engine, session_id, ref.tenant_id, ref.project_id, ref.conversation_id)


def _task_error(view: Optional[dict]) -> Optional[str]:
    found = run_task(view or {})
    if found is None:
        return None
    task = found[1]
    return _error_text(task.get("error") or (task.get("outcome") or {}).get("error"))


def _missing_nodes(execution: WorkflowExecution, view: Optional[dict]) -> list:
    """Graph nodes that neither recorded a step nor were skipped/blocked by the engine."""
    spec = (execution.input_data or {}).get(wer.SPEC_KEY)
    nodes = spec.get("nodes") if isinstance(spec, dict) else None
    if not isinstance(nodes, list):
        return []
    recorded = {step["node_id"] for step in execution.execution_trace or []}
    skipped = {n for n, st in node_states(view or {}).items() if st in ("skipped", "blocked")}
    ids = [str(n.get("id")) for n in nodes if isinstance(n, dict) and n.get("id") is not None]
    return [n for n in ids if n not in recorded and n not in skipped]


async def on_finished(ref: run_handlers.RunRef, snapshot: dict) -> None:
    if ref.tenant_id is None:
        return
    outcome = _outcome(snapshot)
    if outcome is None or outcome in ("queued", "running", "waiting", "resuming"):
        return
    callback = await _settle_finished(ref, snapshot, outcome)  # the session commits on leaving its block
    await send_completion_webhook(callback)


async def _settle_finished(ref: run_handlers.RunRef, snapshot: dict, outcome: str) -> Optional[Dict[str, Any]]:
    """Settle the execution a finished run stands for; its completion callback when this call moved the row."""
    async with wer.tenant_factory(ref.tenant_id)() as db:
        execution = (await db.execute(
            select(WorkflowExecution).where(WorkflowExecution.id == ref.ref_id).with_for_update()
        )).scalar_one_or_none()
        if execution is None or execution.status in TERMINAL:
            return None  # a cancelled row stays cancelled; abort_pending is start_missing's
        if execution.status == "queued":
            return None  # never started: start_missing's
        view = await _view_of(ref, snapshot) if outcome == "completed" else None
        ended = run_ended(view) if view is not None else None
        if outcome == "completed" and ended == "aborted":
            status, error = "cancelled", execution.error or "Execution cancelled"
        elif outcome == "completed" and ended == "faulted":
            # The run task itself faulted (e.g. its finalize could not reach the backend): not a success.
            status, error = "failed", _task_error(view) or "The workflow run task faulted before it could finalize."
        elif outcome == "completed":
            # The task ended without a finalize: judge by the nodes that did run, and by the ones that did not.
            status = _status_from_summaries(_summaries_from_trace(execution))
            error = None
            missing = _missing_nodes(execution, view)
            if missing:
                status = "failed"
                error = ("The workflow run ended before these nodes recorded a result: "
                         f"{', '.join(missing)}.")
        elif outcome == "cancelled":
            status, error = "cancelled", execution.error or "Execution cancelled"
        else:
            status, error = "failed", snapshot.get("reason") or _DEFAULT_ERROR
        moved = await apply_final(db, execution, status, error=error)
        return completion_callback(execution) if moved else None


def register_hooks() -> None:
    run_handlers.register(KIND, on_finished=on_finished)


register_hooks()
