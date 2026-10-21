"""Turn the engine's trace route response into the `Trace` tree graders read.

`flatten` is pure. `fetch_trace` pages the route and merges the pages.

Tree shape: the root conversation is a `subagent` span named `root`. Its
children are, in entry order, one `model` span per assistant entry (usage from
that entry) followed by one `tool` span per tool call in it, and a `compaction`
span per compaction entry. A tool call whose result links a child conversation
(`details.conversationId`) becomes a `subagent` span named after the tool, with
the child's spans as its children. Orphan results (call compacted away) are
ignored; a call with no result is an `interrupted` span.

Usage: spans carry per-request generation usage from entries (so summing spans
never counts a conversation twice). `Trace.usage_by_role` comes from the usage
documents, which also count compaction requests; roles are the subagent tool
names, and the root conversation is `root`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.services.tool_policy import canonical_args_hash

from .engine_api import EngineApi
from .schema import Span, Trace, Usage

ROOT_ROLE = "root"
DEFAULT_MAX_PAGES = 50


class TraceTruncated(Exception):
    """The trace did not fit the response budget; a short tree would mislead graders."""


def _iso(ms: Any) -> str | None:
    if not isinstance(ms, (int, float)) or isinstance(ms, bool):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _entry_usage(u: dict[str, Any] | None) -> Usage:
    u = u or {}
    cost = u.get("cost") or {}
    return Usage(
        input_tokens=int(u.get("inputTokens", 0)),
        output_tokens=int(u.get("outputTokens", 0)),
        cache_read_tokens=int(u.get("cacheReadTokens", 0)),
        cache_write_tokens=int(u.get("cacheWriteTokens", 0)),
        cost_usd=float(cost.get("total", 0.0) or 0.0),
    )


def _tally_usage(t: dict[str, Any] | None) -> Usage:
    t = t or {}
    return Usage(
        input_tokens=int(t.get("inputTokens", 0)),
        output_tokens=int(t.get("outputTokens", 0)),
        cache_read_tokens=int(t.get("cacheReadTokens", 0)),
        cache_write_tokens=int(t.get("cacheWriteTokens", 0)),
        cost_usd=float(t.get("costUsd") or 0.0),
    )


def _add(a: Usage, b: Usage) -> Usage:
    return Usage(
        a.input_tokens + b.input_tokens,
        a.output_tokens + b.output_tokens,
        a.cache_read_tokens + b.cache_read_tokens,
        a.cache_write_tokens + b.cache_write_tokens,
        a.cost_usd + b.cost_usd,
    )


def _spans(
    entries: list[dict[str, Any]], children: dict[str, dict[str, Any]], seen: frozenset[str]
) -> tuple[Span, ...]:
    """Spans of one conversation's entries, in order."""
    results: dict[str, dict[str, Any]] = {}
    result_at: dict[str, Any] = {}
    for e in entries:
        if e.get("kind") == "tool_result":
            p = e.get("payload") or {}
            cid = p.get("toolCallId")
            if isinstance(cid, str) and cid not in results:
                results[cid] = p
                result_at[cid] = e.get("createdAt")

    out: list[Span] = []
    for e in entries:
        kind = e.get("kind")
        p = e.get("payload") or {}
        if kind == "compaction":
            out.append(
                Span(
                    kind="compaction",
                    name="compaction",
                    started_at=_iso(e.get("createdAt")),
                    ended_at=_iso(e.get("createdAt")),
                )
            )
        elif kind == "assistant":
            msg = p.get("message") or {}
            at = _iso(e.get("createdAt"))
            out.append(
                Span(
                    kind="model",
                    name=str(msg.get("model") or "model"),
                    status="partial" if p.get("partial") else "ok",
                    started_at=at,
                    ended_at=at,
                    usage=_entry_usage(p.get("usage")),
                )
            )
            for block in msg.get("blocks") or []:
                if block.get("type") != "tool_call":
                    continue
                out.append(_tool_span(block, at, results, result_at, children, seen))
    return tuple(out)


def _tool_span(
    block: dict[str, Any],
    started: str | None,
    results: dict[str, dict[str, Any]],
    result_at: dict[str, Any],
    children: dict[str, dict[str, Any]],
    seen: frozenset[str],
) -> Span:
    call_id = block.get("id")
    name = str(block.get("name") or "tool")
    args_hash = canonical_args_hash(block.get("arguments") or {})
    res = results.get(call_id) if isinstance(call_id, str) else None
    if res is None:
        return Span(kind="tool", name=name, status="interrupted", args_hash=args_hash, started_at=started)
    status = str(res.get("status") or "ok")
    ended = _iso(result_at.get(call_id))
    details = res.get("details")
    child_id = details.get("conversationId") if isinstance(details, dict) else None
    if isinstance(child_id, str) and child_id in children and child_id not in seen:
        child = children[child_id]
        return Span(
            kind="subagent",
            name=name,
            status=status,
            args_hash=args_hash,
            started_at=started,
            ended_at=ended,
            children=_spans(child.get("entries") or [], children, seen | {child_id}),
        )
    return Span(kind="tool", name=name, status=status, args_hash=args_hash, started_at=started, ended_at=ended)


def flatten(response: dict[str, Any]) -> Trace:
    """Build the `Trace` from a (complete) trace route response."""
    if response.get("truncated"):
        raise TraceTruncated("trace response is truncated; page it with fetch_trace")
    children: dict[str, dict[str, Any]] = response.get("children") or {}
    root_id = str(response.get("conversationId") or "")
    root = Span(
        kind="subagent",
        name=ROOT_ROLE,
        children=_spans(response.get("entries") or [], children, frozenset({root_id})),
    )

    by_role: dict[str, Usage] = {ROOT_ROLE: _tally_usage(response.get("usage"))}
    for child in children.values():
        role = str(child.get("role") or "subagent")
        total = (child.get("usage") or {}).get("total")
        by_role[role] = _add(by_role.get(role, Usage()), _tally_usage(total))

    taint = response.get("taint") or {}
    return Trace(
        root=root,
        taint=bool(taint.get("tainted")),
        usage_by_role=by_role,
        compactions=tuple(response.get("compactions") or ()),
    )


def total_usage(trace: Trace) -> Usage:
    """Whole-run usage: the root plus every subagent role, each counted once."""
    total = Usage()
    for u in trace.usage_by_role.values():
        total = _add(total, u)
    return total


def cache_read_share(trace: Trace) -> float:
    """Cache-read tokens over all prompt tokens (uncached + cache read + cache write)."""
    u = total_usage(trace)
    prompt = u.input_tokens + u.cache_read_tokens + u.cache_write_tokens
    return u.cache_read_tokens / prompt if prompt else 0.0


def _merge_page(into: dict[str, Any], page: dict[str, Any]) -> None:
    into["entries"] = [*into.get("entries", []), *page.get("entries", [])]
    kids: dict[str, Any] = into.setdefault("children", {})
    for cid, child in (page.get("children") or {}).items():
        if cid in kids:
            kids[cid]["entries"] = [*kids[cid]["entries"], *child.get("entries", [])]
        else:
            kids[cid] = child
    for key in ("usage", "taint", "compactions", "agent", "submissions"):
        into[key] = page.get(key, into.get(key))
    into["truncated"] = bool(page.get("truncated"))
    into["nextCursor"] = page.get("nextCursor")


async def fetch_trace(
    api: EngineApi,
    conversation_id: str,
    *,
    limit: int | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    raw: bool = False,
) -> Any:
    """Fetch every page of a conversation's trace and flatten it.

    Raises `TraceTruncated` if `max_pages` pages did not reach the end. With
    `raw=True` returns the merged response dict instead of the `Trace`.
    """
    merged: dict[str, Any] = {"conversationId": conversation_id}
    cursor: str | None = None
    for _ in range(max_pages):
        page = await api.get_trace(conversation_id, limit=limit, cursor=cursor)
        _merge_page(merged, page)
        cursor = merged["nextCursor"]
        if not merged["truncated"] or cursor is None:
            merged["truncated"] = False
            merged["nextCursor"] = None
            break
    return merged if raw else flatten(merged)
