"""Fleet read model (fleet-workflows-on-engine plan, U9): the ``fleet`` ``on_finished`` hook.

Used by ``POST /internal/runs/finished`` (the engine's push) and by the reconciler. It maps the
engine outcome to the job status through ``run_status``, records result, usage and a trace, and
delivers once. Rules:

* A terminal row is never moved (a late settle cannot overwrite ``cancelled``).
* A ``cancelled`` row whose engine run is still active gets its abort re-issued.
* A ``pending`` row with no conversation never started: the start is retried here.
* Idempotent: only the call that moves the row delivers.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.models.background_job import BackgroundJob, JobStatus
from app.models.custom_agent import CustomAgent
from app.services import fleet_engine as fe
from app.services import run_handlers
from app.services.deep_work import engine_view
from app.services.engine_client import get_engine_client
from app.services.run_status import settled_status

logger = logging.getLogger(__name__)

KIND = fe.KIND
_TERMINAL = (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value)
_PREVIEW = 500
MAX_ROUNDS_MESSAGE = "The agent stopped after reaching its maximum number of steps (max_rounds)."
_DEFAULT_ERROR = "The engine run failed."


async def read_view(ref: run_handlers.RunRef) -> dict:
    engine = get_engine_client()
    session_id = ref.session_id or engine.directory.default_session_id
    return await engine_view.read_snapshot(engine, session_id, ref.tenant_id, ref.project_id, ref.conversation_id)


def _active(view: dict) -> bool:
    from app.services.run_reconciler import view_is_active
    return view_is_active(view)


def _outcome(snapshot: dict, view: Optional[dict]) -> str:
    """The run-status enum value for a settled run."""
    return settled_status(snapshot, view or {}) or "completed"


def _error_message(snapshot: dict, view: Optional[dict]) -> str:
    entries = (view or {}).get("entries") or []
    for e in reversed(entries):
        if e.get("kind") == "user":
            break
        if e.get("kind") == "run_error":
            err = (e.get("payload") or {}).get("error") or {}
            blob = f"{err.get('kind', '')} {err.get('message', '')}"
            if "max_rounds" in blob:
                return MAX_ROUNDS_MESSAGE
            if err.get("message"):
                return str(err["message"])[:2000]
    reason = snapshot.get("reason")
    if reason and "max_rounds" in str(reason):
        return MAX_ROUNDS_MESSAGE
    return str(reason)[:2000] if reason else _DEFAULT_ERROR


def _blocks(message: dict) -> list:
    return message.get("blocks") or []


def _text(message: dict) -> str:
    return "".join(b.get("text", "") for b in _blocks(message) if b.get("type") == "text")


def final_answer(view: Optional[dict], job_id: Any) -> Optional[str]:
    # The run's final answer: the last one after every placed steer. ``answer_for`` stops at the first
    # steer, which would hand back the answer the steer superseded.
    final = _answer_after_steer(view, fe.request_id(job_id))
    if final is not None:
        return final
    answer = engine_view.answer_for(view or {}, fe.request_id(job_id))
    return answer[1] if answer and answer[0] == "done" else None


def _answer_after_steer(view: Optional[dict], request_id: str) -> Optional[str]:
    """The last tool-free assistant message after the task, steers included.

    ``answer_for`` stops at the next user entry, which for a Fleet run is a steer (an intervene), and the
    answer to a steered run is written after it.
    """
    entries = (view or {}).get("entries") or []
    start = None
    for i, e in enumerate(entries):
        p = e.get("payload") or {}
        if e.get("kind") == "user" and request_id in (p.get("requestId"), p.get("submissionId")):
            start = i
    if start is None:
        return None
    final = None
    for e in entries[start + 1:]:
        p = e.get("payload") or {}
        msg = p.get("message") or {}
        if e.get("kind") == "assistant" and msg and not p.get("partial") \
                and not any(b.get("type") == "tool_call" for b in _blocks(msg)):
            final = _text(msg)
    return final


def _tokens(usage: Optional[dict]) -> dict:
    total = (usage or {}).get("total") or {}
    inp, out = int(total.get("inputTokens") or 0), int(total.get("outputTokens") or 0)
    return {"input": inp, "output": out, "total": inp + out}


def build_trace(view: Optional[dict], usage: Optional[dict]) -> list:
    """Assistant and tool entries as ``ExecutionTraceStep`` dicts, in order; usage rides on a final step."""
    steps: list = []
    results: dict = {}
    entries = (view or {}).get("entries") or []
    for e in entries:
        if e.get("kind") == "tool_result":
            p = e.get("payload") or {}
            body = p.get("output") if p.get("output") is not None else p.get("content")
            if body is None and isinstance(p.get("message"), dict):
                body = _text(p["message"])
            results[p.get("toolCallId")] = str(body if body is not None else "")[:_PREVIEW]
    for e in entries:
        if e.get("kind") != "assistant":
            continue
        p = e.get("payload") or {}
        msg = p.get("message") or {}
        if p.get("partial"):
            continue
        text = _text(msg)
        if text:
            steps.append({"step": len(steps) + 1, "step_id": f"s{len(steps) + 1}", "node_id": f"text-{len(steps) + 1}",
                          "node_type": "text_response", "status": "completed", "type": "text_response",
                          "content_preview": text[:_PREVIEW], "reasoning": None})
        for b in _blocks(msg):
            if b.get("type") != "tool_call":
                continue
            name = b.get("name") or b.get("toolName") or "tool"
            steps.append({"step": len(steps) + 1, "step_id": f"s{len(steps) + 1}", "node_id": str(b.get("id") or f"tool-{len(steps) + 1}"),
                          "node_type": "tool_call", "node_name": name, "status": "completed", "type": "tool_call",
                          "tool_name": name, "tool_args": b.get("input") or b.get("arguments") or {},
                          "tool_result_preview": results.get(b.get("id"), "")})
    tokens = _tokens(usage)
    if tokens["total"]:
        steps.append({"step": len(steps) + 1, "step_id": f"s{len(steps) + 1}", "node_id": "usage", "node_type": "usage",
                      "status": "completed", "type": "usage", "token_usage": tokens})
    return steps


async def deliver(job: BackgroundJob, text: str) -> None:
    """Hand the result to output gating once; delivery trouble never changes the job."""
    try:
        async with fe.tenant_factory(job.tenant_id)() as db:
            agent = await db.get(CustomAgent, job.agent_id) if job.agent_id else None
            from app.services.output_gating import evaluate_and_deliver
            await evaluate_and_deliver(
                job_id=job.id, agent_id=job.agent_id, agent_name=agent.name if agent else "Unknown",
                result_text=text, input_prompt=fe.task_text(job.input_data),
                background_config=(agent.background_config if agent else None) or {}, db=db,
                tenant_id=job.tenant_id)
    except Exception:
        logger.exception("fleet job %s: delivery failed", job.id)


async def _reabort(ref: run_handlers.RunRef, snapshot: dict) -> None:
    """A cancelled job: abort again while the engine run is still active; drop the pending mark once settled."""
    view = snapshot.get("view")
    if view is None:
        try:
            view = await read_view(ref)
        except Exception as e:  # engine unreachable or no view: leave the mark for the next pass
            logger.warning("fleet job %s: cannot read the engine run: %s", ref.ref_id, e)
            return
    async with fe.tenant_factory(ref.tenant_id)() as db:
        job = await db.get(BackgroundJob, ref.ref_id, with_for_update=True)
        if job is None or job.status != JobStatus.CANCELLED.value:
            return
        if _active(view) and not await fe.abort_run(db, job, job.error):
            return
        if (job.input_data or {}).get("abort_pending"):
            fe.set_abort_pending(job, False)


async def on_finished(ref: run_handlers.RunRef, snapshot: dict) -> None:
    if ref.tenant_id is None:
        return
    async with fe.tenant_factory(ref.tenant_id)() as db:
        job = await db.get(BackgroundJob, ref.ref_id)
        if job is None:
            return
        status, has_conversation = job.status, job.conversation_id is not None
    if status == JobStatus.CANCELLED.value:
        if has_conversation:
            await _reabort(ref, snapshot)
        return
    if status in _TERMINAL:
        return
    if not has_conversation:
        if status == JobStatus.PENDING.value:
            await fe.start_fleet_run(fe.tenant_factory(ref.tenant_id), ref.ref_id)
        return

    view = snapshot.get("view")
    if view is None:
        view = await read_view(ref)  # engine trouble propagates: the push is retried
    if _active(view):
        return
    outcome = _outcome(snapshot, view)
    usage = snapshot.get("usage") or view.get("usage")
    deliver_text: Optional[str] = None
    async with fe.tenant_factory(ref.tenant_id)() as db:
        job = await db.get(BackgroundJob, ref.ref_id, with_for_update=True)
        if job is None or job.status in _TERMINAL:
            return
        now = datetime.now(timezone.utc)
        job.execution_trace = build_trace(view, usage)
        job.completed_at = now
        job.progress = 100
        job.status = outcome
        if outcome == JobStatus.COMPLETED.value:
            text = final_answer(view, job.id) or "Agent completed without final output."
            job.result = {"output": text, "total_tokens": _tokens(usage)["total"]}
            deliver_text = text
        elif outcome == JobStatus.FAILED.value:
            job.error = _error_message(snapshot, view)
            deliver_text = f"Agent execution failed: {job.error}"
        else:
            job.status = JobStatus.CANCELLED.value
        if job.input_data and job.input_data.get("abort_pending"):
            fe.set_abort_pending(job, False)
        snap_job = job
    if deliver_text is not None:
        await deliver(snap_job, deliver_text)


def register_hooks() -> None:
    run_handlers.register(KIND, on_finished=on_finished)


register_hooks()
