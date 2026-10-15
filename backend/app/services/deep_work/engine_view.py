"""Reading the engine's watch view from the backend (deep-work-on-engine plan, U7).

The engine has no "state" endpoint: a conversation's committed state is its watch view, delivered as
a snapshot followed by JSON Patch frames (``engine/src/watch/``). The backend follows that stream to
(a) wait for an intake / plan answer, (b) read the run state for lazy reconcile (KTD11) and
(c) check that a child conversation belongs to the root before proxying its watch (R15).
"""
import asyncio
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.deep_work_state import EngineRunState


class EngineRunFailed(Exception):
    """The run that handled a submission ended with ``run_error`` (message is safe to show)."""


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def apply_patch(doc: Any, ops: List[Dict[str, Any]]) -> Any:
    """Apply the engine's ``add`` / ``replace`` / ``remove`` operations (RFC 6902 subset) in place."""
    for op in ops:
        path = op["path"]
        if path == "":
            doc = op["value"]
            continue
        *parents, last = [_unescape(t) for t in path.split("/")[1:]]
        target = doc
        for token in parents:
            target = target[int(token)] if isinstance(target, list) else target[token]
        kind = op["op"]
        if isinstance(target, list):
            idx = len(target) if last == "-" else int(last)
            if kind == "add":
                target.insert(idx, op["value"])
            elif kind == "replace":
                target[idx] = op["value"]
            else:
                del target[idx]
        elif kind == "remove":
            target.pop(last, None)
        else:
            target[last] = op["value"]
    return doc


def parse_frames(buffer: str) -> Tuple[List[Dict[str, Any]], str]:
    """Complete SSE frames in ``buffer`` (their JSON ``data``), and the unfinished remainder."""
    frames: List[Dict[str, Any]] = []
    while "\n\n" in buffer:
        raw, buffer = buffer.split("\n\n", 1)
        data = "\n".join(line[5:].lstrip() for line in raw.splitlines() if line.startswith("data:"))
        if not data:
            continue  # heartbeat comment
        try:
            frames.append(json.loads(data))
        except ValueError:
            continue
    return frames, buffer


async def follow(
    engine: Any, session_id: Any, tenant_id: Any, project_id: Any, conversation_id: Any, *,
    until: Optional[Callable[[Dict[str, Any]], bool]] = None, timeout: float = 20.0,
) -> Dict[str, Any]:
    """Follow the watch stream until ``until(view)`` (the first snapshot when ``None``); returns the view.

    Raises ``asyncio.TimeoutError`` when the condition is not met in ``timeout`` seconds.
    """

    async def run() -> Dict[str, Any]:
        upstream = await engine.watch_stream(session_id, tenant_id, project_id, conversation_id)
        view: Optional[Dict[str, Any]] = None
        buffer = ""
        try:
            async for chunk in upstream.aiter_raw():
                buffer += chunk.decode("utf-8", "replace").replace("\r\n", "\n")
                frames, buffer = parse_frames(buffer)
                for frame in frames:
                    if "snapshot" in frame:
                        view = frame["snapshot"]
                    elif view is not None and "patch" in frame:
                        view = apply_patch(view, frame["patch"])
                    if view is not None and (until is None or until(view)):
                        return view
        finally:
            await upstream.aclose()
        raise asyncio.TimeoutError("watch stream ended before the condition was met")

    return await asyncio.wait_for(run(), timeout)


async def read_snapshot(engine: Any, session_id: Any, tenant_id: Any, project_id: Any, conversation_id: Any) -> Dict[str, Any]:
    return await follow(engine, session_id, tenant_id, project_id, conversation_id)


# --- reading a view ----------------------------------------------------------------------------


def _text_of(message: Dict[str, Any]) -> str:
    return "".join(b.get("text", "") for b in message.get("blocks", []) if b.get("type") == "text")


def _has_tool_call(message: Dict[str, Any]) -> bool:
    return any(b.get("type") == "tool_call" for b in message.get("blocks", []))


def engine_run_state(view: Dict[str, Any]) -> EngineRunState:
    """Active, or how the last run ended (``run_error``, ``aborted``, ``finished``) for reconcile (KTD11)."""
    inbox = view.get("inbox") or {}
    if (view.get("run") or {}).get("active") or inbox.get("queue"):
        return EngineRunState(active=True)  # a queued input starts a run: not ended yet
    last_user = -1
    entries = view.get("entries") or []
    for i, e in enumerate(entries):
        if e.get("kind") == "user":
            last_user = i
    for e in reversed(entries[last_user + 1:]):
        if e.get("kind") == "run_error":
            err = (e.get("payload") or {}).get("error") or {}
            if err.get("kind") == "aborted":
                return EngineRunState(active=False, ended="aborted")
            return EngineRunState(active=False, ended="run_error", error=str(err.get("message") or "The engine run failed."))
    return EngineRunState(active=False, ended="finished")


def answer_for(view: Dict[str, Any], request_id: str) -> Optional[Tuple[str, str]]:
    """``("done", text)`` / ``("error", message)`` once the submission with ``request_id`` has been answered.

    ``None`` while it is queued or its run is active.
    """
    entries = view.get("entries") or []
    start = None
    for i, e in enumerate(entries):
        p = e.get("payload") or {}
        if e.get("kind") == "user" and request_id in (p.get("requestId"), p.get("submissionId")):
            start = i
    if start is None or (view.get("run") or {}).get("active"):
        return None
    for e in entries[start + 1:]:
        if e.get("kind") == "user":
            break
        if e.get("kind") == "run_error":
            err = (e.get("payload") or {}).get("error") or {}
            return "error", str(err.get("message") or "The engine run failed.")
    final = None
    for e in entries[start + 1:]:
        if e.get("kind") == "user":
            break
        msg = (e.get("payload") or {}).get("message")
        if e.get("kind") == "assistant" and msg and not (e.get("payload") or {}).get("partial") and not _has_tool_call(msg):
            final = _text_of(msg)
    return ("done", final) if final is not None else None


def subagent_conversation_ids(view: Dict[str, Any]) -> set:
    return {str(s.get("conversationId")) for s in (view.get("subagents") or {}).values() if s.get("conversationId")}
