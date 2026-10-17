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
from app.services.deep_work import engine_view
from app.services.run_status import from_engine_task

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
        await db.commit()
        return moved


def _outcome(snapshot: dict) -> Optional[str]:
    """The run status a settled snapshot stands for; None while the engine still has work or the status is unknown."""
    status = snapshot.get("runStatus")
    if status:
        return status
    if snapshot.get("source") == "push":
        if snapshot.get("status") == "done":
            return from_engine_task("terminal", outcome="done")
        return from_engine_task("terminal", outcome="aborted" if snapshot.get("reason") == "aborted" else "faulted")
    if snapshot.get("view") is not None:  # the reconciler calls once nothing is active
        ended = engine_view.engine_run_state(snapshot["view"]).ended
        return from_engine_task("terminal", outcome={"aborted": "aborted", "run_error": "faulted"}.get(ended, "done"))
    return None


async def on_finished(ref: run_handlers.RunRef, snapshot: dict) -> None:
    if ref.tenant_id is None:
        return
    outcome = _outcome(snapshot)
    if outcome is None or outcome in ("queued", "running", "waiting", "resuming"):
        return
    async with wer.tenant_factory(ref.tenant_id)() as db:
        execution = (await db.execute(
            select(WorkflowExecution).where(WorkflowExecution.id == ref.ref_id).with_for_update()
        )).scalar_one_or_none()
        if execution is None or execution.status in TERMINAL:
            return  # a cancelled row stays cancelled; abort_pending is start_missing's
        if execution.status == "queued":
            return  # never started: start_missing's
        if outcome == "completed":
            # The task ended without a finalize: judge by the nodes that did run.
            status = _status_from_summaries(_summaries_from_trace(execution))
            error = None
        elif outcome == "cancelled":
            status, error = "cancelled", execution.error or "Execution cancelled"
        else:
            status, error = "failed", snapshot.get("reason") or _DEFAULT_ERROR
        await apply_final(db, execution, status, error=error)


def register_hooks() -> None:
    run_handlers.register(KIND, on_finished=on_finished)


register_hooks()
