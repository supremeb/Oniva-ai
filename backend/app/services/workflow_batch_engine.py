"""``/execute-batch`` on the engine (fleet-workflows-on-engine plan, U18).

One batch is one engine run. Its graph is a single ``batch`` fan-out node over the items
(``parallel = parallel_limit``, ``failFast = false``); each item runs as a child ``workflow_run`` in a
fan-out lane (U34). The run is the execution row whose id is the batch id (``trigger = "batch"``, the
frozen synthetic spec in ``input_data["__spec"]``) and an ``engine_conversations`` row of kind ``batch``.

Item data never passes through the engine (KTD17). The gateway's fan-out ``prepare`` (U13) reads
``workflow_batch_items`` and creates one item ``workflow_executions`` row per item under the deterministic id
``uuid5(batch_id, "batch:{index}")``; the item child run reads its inputs from that row and its finalize
writes the result there. This module copies the item rows' state back into ``workflow_batch_items`` and the
batch counters (``sync_items``), so ``GET /batches/{id}`` and ``/items`` are plain reads of those tables.

Registers the ``batch`` hooks: ``on_finished`` applies a settled run once (and sends the completion
webhook on that one transition), ``start_missing`` re-issues a start that never happened (or an abort that
never got through), ``find_stuck`` lists quiet rows, ``fail`` is the terminal failure.

Status mapping (the batch enums are unchanged): all items completed is ``completed``; some failed is
``partially_failed`` (the plan's "completed with errors"); every item failed is ``failed``; an item that
never ran because the batch was cancelled is ``skipped`` (the item enum has no ``cancelled``).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select, update

from app.internal_api import workflow_contract as wc
from app.models.workflow import WorkflowExecution
from app.models.workflow_batch import BatchItemStatus, BatchStatus, WorkflowBatch, WorkflowBatchItem
from app.services import run_handlers
from app.services import workflow_engine_runs as wer
from app.services.automation_scope import bind_run_conversation, run_conversation_id
from app.services.engine_client import EngineError, run_start_body
from app.services.run_status import settled_status
from app.services.webhook_service import WebhookConfig, WebhookPayload, WebhookService
from app.services.workflow_compiler import build_run_input, compile_for_run, resolve_spec

logger = logging.getLogger(__name__)

KIND = "batch"
NODE_ID = "batch"
STUCK_BATCH = 200
MAX_ITEMS = wc.MAX_FANOUT_ITEMS
MAX_PARALLEL = wc.MAX_PARALLEL
TERMINAL_BATCH = (BatchStatus.COMPLETED, BatchStatus.PARTIALLY_FAILED, BatchStatus.FAILED, BatchStatus.CANCELLED)
TERMINAL_ITEM = (BatchItemStatus.COMPLETED, BatchItemStatus.FAILED, BatchItemStatus.SKIPPED)
_ITEM_FROM_EXECUTION = {
    "completed": BatchItemStatus.COMPLETED,
    "failed": BatchItemStatus.FAILED,
    "cancelled": BatchItemStatus.SKIPPED,
    "running": BatchItemStatus.RUNNING,
    "pending": BatchItemStatus.PENDING,
    "queued": BatchItemStatus.PENDING,
}
_EXECUTION_TERMINAL = ("completed", "failed", "cancelled")
WEBHOOK_RETRIES = 2


def tenant_factory(tenant_id: Any) -> Callable:
    return wer.tenant_factory(tenant_id)  # looked up per call: tests replace it on ``workflow_engine_runs``


def item_execution_id(batch_id: Any, index: int) -> uuid.UUID:
    """The item's execution row id; the one the gateway's fan-out ``prepare`` creates (a batch has one attempt)."""
    return wc.fanout_item_id(wer._uuid(batch_id), NODE_ID, index)


def clamp_parallel(value: Any) -> int:
    try:
        return max(1, min(int(value), MAX_PARALLEL))
    except (TypeError, ValueError):
        return 1


def batch_spec(batch: WorkflowBatch) -> Dict[str, Any]:
    """The synthetic one-node spec of a batch run (the gateway reads the node's ``data`` from it)."""
    return {
        "nodes": [{
            "id": NODE_ID, "type": "batch", "position": {"x": 0, "y": 0},
            "data": {"batchId": str(batch.id), "workflowId": str(batch.workflow_id),
                     "parallel": clamp_parallel(batch.parallel_limit)},
        }],
        "edges": [],
    }


async def compile_batch(db, tenant_id: Any, workflow_id: Any) -> Dict[str, Any]:
    """The compiled graph: one ``batch`` fan-out node plus the item workflow under ``workflows``.

    Raises ``ValueError`` for a workflow the engine cannot run (invalid, too large, missing subworkflow).
    """
    tenant_id, workflow_id = wer._uuid(tenant_id), wer._uuid(workflow_id)
    spec = await resolve_spec(db, tenant_id, workflow_id)
    if spec is None:
        raise ValueError("Workflow not found")
    child = await compile_for_run(db, tenant_id, spec, workflow_id=workflow_id)
    node = {
        "id": NODE_ID, "type": "batch", "deps": [], "mayFanOut": True,
        "policy": {"maxRetries": 0, "backoffMs": 0, "timeoutMs": 60_000, "onError": "stop"},
    }
    return {
        "nodes": [node], "subgraphs": {},
        "workflows": {str(workflow_id): {k: child[k] for k in ("nodes", "subgraphs", "workflows")}},
        "errorMode": "continue", "warnings": child.get("warnings", []),
    }


def new_execution(batch: WorkflowBatch, user_id: Any) -> WorkflowExecution:
    """The run's execution row (id = batch id). The caller adds it in the same transaction as the batch."""
    spec = batch_spec(batch)
    return WorkflowExecution(
        id=batch.id, workflow_id=batch.workflow_id, user_id=wer._uuid(user_id), tenant_id=batch.tenant_id,
        engine_conversation_id=run_conversation_id(KIND, batch.id), status="queued", trigger="batch",
        input_data={wer.SPEC_KEY: spec}, results={}, execution_trace=[], total_nodes=1,
        started_at=datetime.now(timezone.utc),
    )


def _tag(batch_id: Any) -> dict:
    return {"metadata": {"notify": "backend", "runKind": KIND, "batchId": str(batch_id)}, "lane": "background"}


async def is_engine_batch(db, batch_id: Any) -> bool:
    execution = await db.get(WorkflowExecution, wer._uuid(batch_id))
    return execution is not None and execution.trigger == "batch" and execution.engine_conversation_id is not None


# ---- start ----------------------------------------------------------------------------------------------


async def start_batch_run(factory: Callable, batch_id: Any, *, engine: Any = None) -> run_handlers.StartResult:
    """Start (or idempotently re-start) the engine run of a ``queued`` batch. Never raises for engine trouble.

    The batch turns ``running`` only after the engine accepted the start, so a ``queued`` batch may or may not
    have an engine run (a crash between the two); the deterministic ids make the repeat answer the existing one.
    """
    batch_id = wer._uuid(batch_id)
    async with factory() as db:
        batch = await db.get(WorkflowBatch, batch_id)
        execution = await db.get(WorkflowExecution, batch_id)
        if batch is None or execution is None:
            return run_handlers.StartResult.failed("batch_not_found")
        if batch.status != BatchStatus.QUEUED:
            return run_handlers.StartResult.already_started()
        tenant_id, user_id, workflow_id = batch.tenant_id, execution.user_id, batch.workflow_id
        if workflow_id is None:
            return run_handlers.StartResult.failed("workflow_missing")
        try:
            compiled = await compile_batch(db, tenant_id, workflow_id)
        except ValueError as exc:
            return run_handlers.StartResult.failed(f"invalid_workflow: {exc}")

    engine = engine or wer.get_engine_client()
    try:
        conversation_id, project_id, session_id = await bind_run_conversation(
            factory, KIND, batch_id, tenant_id, user_id)
        body = run_start_body(wer.TASK_KIND, conversation_id, build_run_input(batch_id, compiled),
                              agent=_tag(batch_id))
        started = await engine.start_run(session_id, tenant_id, project_id, body, f"{KIND}:{batch_id}")
    except (EngineError, OSError, TimeoutError) as exc:
        code = wer._failure_code(exc) if isinstance(exc, EngineError) else "engine_unreachable"
        logger.warning("batch %s could not start on the engine: %s", batch_id, exc)
        return run_handlers.StartResult.failed(code)

    async with factory() as db:
        batch = await db.get(WorkflowBatch, batch_id, with_for_update=True)
        execution = await db.get(WorkflowExecution, batch_id)
        if batch is not None and batch.status == BatchStatus.QUEUED:
            batch.status = BatchStatus.RUNNING
            batch.started_at = batch.started_at or datetime.now(timezone.utc)
            batch.error_message = None
        if execution is not None and execution.status == "queued":
            execution.status = "running"
            execution.engine_conversation_id = conversation_id
    task_id = started.get("taskId") if isinstance(started, dict) else None
    return run_handlers.StartResult.started(task_id)


# ---- item state -----------------------------------------------------------------------------------------


async def _ordered_items(db, batch_id: uuid.UUID) -> List[WorkflowBatchItem]:
    return list((await db.execute(
        select(WorkflowBatchItem).where(WorkflowBatchItem.batch_id == batch_id).order_by(WorkflowBatchItem.sequence)
    )).scalars().all())


async def sync_items(db, batch: WorkflowBatch) -> List[WorkflowBatchItem]:
    """Copy each item execution row's state onto its batch item; a terminal item never moves.

    Returns the ordered items. The caller sets the batch status and commits.
    """
    items = await _ordered_items(db, batch.id)
    ids = [item_execution_id(batch.id, i) for i in range(len(items))]
    rows: Dict[uuid.UUID, WorkflowExecution] = {}
    if ids:
        rows = {r.id: r for r in (await db.execute(
            select(WorkflowExecution).where(WorkflowExecution.tenant_id == batch.tenant_id,
                                            WorkflowExecution.id.in_(ids))
        )).scalars().all()}
    for item, iid in zip(items, ids):
        row = rows.get(iid)
        if row is None or item.status in TERMINAL_ITEM:
            continue
        mapped = _ITEM_FROM_EXECUTION.get(row.status)
        if mapped is None or mapped == item.status:
            continue
        item.status = mapped
        item.started_at = item.started_at or row.started_at
        if mapped in TERMINAL_ITEM:
            item.completed_at = row.completed_at or datetime.now(timezone.utc)
            item.results = row.results or None
            item.error_message = row.error
            if row.started_at is not None and row.completed_at is not None:
                started, done = row.started_at, row.completed_at
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                if done.tzinfo is None:
                    done = done.replace(tzinfo=timezone.utc)
                item.execution_time_ms = max(0, int((done - started).total_seconds() * 1000))
    return items


def apply_counts(batch: WorkflowBatch, items: List[WorkflowBatchItem]) -> None:
    batch.completed_count = sum(1 for i in items if i.status == BatchItemStatus.COMPLETED)
    batch.failed_count = sum(1 for i in items if i.status == BatchItemStatus.FAILED)
    batch.pending_count = sum(1 for i in items if i.status in (BatchItemStatus.PENDING, BatchItemStatus.RUNNING))


async def refresh_progress(db, batch: WorkflowBatch) -> None:
    """Live counters for a running engine batch (a read model; the status moves only in ``on_finished``)."""
    if batch.status not in (BatchStatus.QUEUED, BatchStatus.RUNNING) or not await is_engine_batch(db, batch.id):
        return
    apply_counts(batch, await sync_items(db, batch))
    await db.commit()


def _close_open_items(items: List[WorkflowBatchItem], status: BatchItemStatus, error: Optional[str]) -> None:
    now = datetime.now(timezone.utc)
    for item in items:
        if item.status not in TERMINAL_ITEM:
            item.status = status
            item.completed_at = now
            if error and status == BatchItemStatus.FAILED:
                item.error_message = error


def _final_status(batch: WorkflowBatch) -> BatchStatus:
    if batch.failed_count == 0:
        return BatchStatus.COMPLETED
    return BatchStatus.FAILED if batch.completed_count == 0 else BatchStatus.PARTIALLY_FAILED


# ---- cancel ---------------------------------------------------------------------------------------------


async def cancel_batch_run(db, batch: WorkflowBatch, *, engine: Any = None) -> bool:
    """Abort the engine run, then cancel the batch (the caller commits). False for a batch without an engine run.

    Finished items keep their results; the rest become ``skipped``, and their item executions are cancelled
    so nothing resurrects them. An abort the engine could not take is left to ``start_missing``.
    """
    execution = await db.get(WorkflowExecution, batch.id)
    if execution is None or execution.trigger != "batch" or execution.engine_conversation_id is None:
        return False
    await wer.cancel_workflow_run(db, execution, "Batch cancelled by user", engine=engine)
    items = await sync_items(db, batch)
    open_ids = [item_execution_id(batch.id, i) for i, it in enumerate(items) if it.status not in TERMINAL_ITEM]
    if open_ids:
        await db.execute(
            update(WorkflowExecution)
            .where(WorkflowExecution.id.in_(open_ids), WorkflowExecution.status.notin_(_EXECUTION_TERMINAL))
            .values(status="cancelled", completed_at=datetime.now(timezone.utc), error="Batch cancelled by user")
        )
    _close_open_items(items, BatchItemStatus.SKIPPED, None)
    apply_counts(batch, items)
    batch.status = BatchStatus.CANCELLED
    batch.completed_at = datetime.now(timezone.utc)
    return True


# ---- registry hooks -------------------------------------------------------------------------------------


def _ref(batch: WorkflowBatch, execution: WorkflowExecution) -> run_handlers.RunRef:
    extra = {"abort_pending": True} if (execution.input_data or {}).get("abort_pending") else {}
    return run_handlers.RunRef(kind=KIND, ref_id=batch.id, tenant_id=batch.tenant_id,
                               conversation_id=execution.engine_conversation_id, extra=extra)


async def find_stuck(session, older_than: timedelta) -> list:
    """Engine batches quiet long enough to look at: not started, running (a settle whose push was lost) and
    cancelled batches whose engine abort never got through."""
    cutoff = datetime.now(timezone.utc) - older_than
    live = (await session.execute(
        select(WorkflowBatch, WorkflowExecution)
        .join(WorkflowExecution, WorkflowExecution.id == WorkflowBatch.id)
        .where(WorkflowBatch.status.in_((BatchStatus.QUEUED, BatchStatus.RUNNING)),
               WorkflowExecution.trigger == "batch", WorkflowExecution.engine_conversation_id.is_not(None),
               WorkflowBatch.updated_at < cutoff)
        .order_by(WorkflowBatch.updated_at).limit(STUCK_BATCH)
    )).all()
    cancelled = (await session.execute(
        select(WorkflowBatch, WorkflowExecution)
        .join(WorkflowExecution, WorkflowExecution.id == WorkflowBatch.id)
        .where(WorkflowBatch.status == BatchStatus.CANCELLED, WorkflowExecution.trigger == "batch",
               WorkflowExecution.engine_conversation_id.is_not(None),
               WorkflowBatch.completed_at < cutoff)
        .order_by(WorkflowBatch.completed_at).limit(STUCK_BATCH)
    )).all()
    refs = [_ref(b, e) for b, e in live]
    refs += [_ref(b, e) for b, e in cancelled if (e.input_data or {}).get("abort_pending")]
    return refs


async def _bump_attempts(ref: run_handlers.RunRef) -> int:
    async with tenant_factory(ref.tenant_id)() as db:
        execution = await db.get(WorkflowExecution, ref.ref_id)
        if execution is None:
            return 0
        data = dict(execution.input_data or {})
        data["start_attempts"] = int(data.get("start_attempts") or 0) + 1
        execution.input_data = data
        return data["start_attempts"]


async def start_missing(ref: run_handlers.RunRef) -> run_handlers.StartResult:
    if ref.tenant_id is None:
        return run_handlers.StartResult.failed("tenant_unknown")
    async with tenant_factory(ref.tenant_id)() as db:
        batch = await db.get(WorkflowBatch, ref.ref_id)
        execution = await db.get(WorkflowExecution, ref.ref_id)
        if batch is None or execution is None:
            return run_handlers.StartResult.failed("batch_not_found")
        status = batch.status
        retry_abort = status == BatchStatus.CANCELLED and bool((execution.input_data or {}).get("abort_pending"))
        if retry_abort:
            if not await wer.abort_run(db, execution, execution.error):
                return run_handlers.StartResult.failed("abort_unavailable")
            wer.set_abort_pending(execution, False)
            return run_handlers.StartResult.already_started()
    # RUNNING means the engine already accepted the start (the status moves only after it did).
    result = await start_batch_run(tenant_factory(ref.tenant_id), ref.ref_id)
    if result.status == "failed":
        attempts = await _bump_attempts(ref)
        return run_handlers.StartResult.failed(result.error or "engine_unreachable", attempts)
    return result


async def fail(ref: run_handlers.RunRef, code: str) -> None:
    """Terminal failure (engine conversation missing, or start retries ran out)."""
    if ref.tenant_id is None:
        return
    async with tenant_factory(ref.tenant_id)() as db:
        batch = await db.get(WorkflowBatch, ref.ref_id, with_for_update=True)
        if batch is None or batch.status in TERMINAL_BATCH:
            return
        items = await sync_items(db, batch)
        _close_open_items(items, BatchItemStatus.SKIPPED, None)
        apply_counts(batch, items)
        batch.status = BatchStatus.FAILED
        batch.error_message = code[:2000]
        batch.completed_at = datetime.now(timezone.utc)
        execution = await db.get(WorkflowExecution, ref.ref_id)
        if execution is not None:
            wer.settle(execution, "failed", error=code)
        payload = _webhook_payload(batch)
        hook = (batch.webhook_url, batch.webhook_secret)
    await _notify(hook, payload)


def _webhook_payload(batch: WorkflowBatch) -> WebhookPayload:
    event = {"completed": "batch.completed", "partially_failed": "batch.partially_failed"}.get(
        batch.status.value, "batch.failed")
    return WebhookPayload(
        event_type=event, workflow_id=str(batch.workflow_id) if batch.workflow_id else None,
        execution_id=str(batch.id), tenant_id=str(batch.tenant_id), status=batch.status.value,
        message=batch.error_message,
        results={"batch_id": str(batch.id), "total_count": batch.total_count,
                 "completed_count": batch.completed_count, "failed_count": batch.failed_count},
    )


async def _notify(hook: tuple, payload: WebhookPayload) -> None:
    url, secret = hook
    if not url:
        return
    service = WebhookService()
    try:
        await service.send_webhook(WebhookConfig(url=url, secret=secret, retry_count=WEBHOOK_RETRIES), payload)
    except Exception:
        logger.exception("batch %s: completion webhook failed", payload.execution_id)
    finally:
        await service.close()


async def on_finished(ref: run_handlers.RunRef, snapshot: dict) -> None:
    """Apply a settled engine run to the batch, once. A terminal batch never moves; only the call that
    moves it sends the completion webhook."""
    if ref.tenant_id is None:
        return
    run_status = settled_status(snapshot)  # not the raw push status ("done" / "unanswered")
    if run_status is None or run_status in ("queued", "running", "waiting", "resuming"):
        return
    async with tenant_factory(ref.tenant_id)() as db:
        batch = await db.get(WorkflowBatch, ref.ref_id, with_for_update=True)
        if batch is None or batch.status in TERMINAL_BATCH:
            return
        execution = await db.get(WorkflowExecution, ref.ref_id)
        items = await sync_items(db, batch)
        error: Optional[str] = None
        if run_status == "cancelled":
            _close_open_items(items, BatchItemStatus.SKIPPED, None)
            apply_counts(batch, items)
            batch.status = BatchStatus.CANCELLED
        else:
            if run_status == "failed":
                error = str(snapshot.get("reason") or "engine_run_failed")
            elif any(i.status not in TERMINAL_ITEM for i in items):
                error = "item_not_finalized"
            _close_open_items(items, BatchItemStatus.FAILED, error)
            apply_counts(batch, items)
            batch.status = _final_status(batch) if error is None or batch.completed_count else BatchStatus.FAILED
            batch.error_message = error
        batch.completed_at = datetime.now(timezone.utc)
        if execution is not None:
            wer.settle(execution, "cancelled" if batch.status == BatchStatus.CANCELLED
                       else ("completed" if error is None else "failed"), error=error)
        payload = None if batch.status == BatchStatus.CANCELLED else _webhook_payload(batch)
        hook = (batch.webhook_url, batch.webhook_secret)
    if payload is not None:
        await _notify(hook, payload)


def register_hooks() -> None:
    run_handlers.register(KIND, on_finished=on_finished, start_missing=start_missing,
                          find_stuck=find_stuck, fail=fail)


register_hooks()
