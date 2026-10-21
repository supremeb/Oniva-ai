"""Instruction-shaped text detector for project memory (plan U27 / KTD18).

Memory text is later injected into model prompts, so text that reads like a command to the assistant
(or carries a URL, an email address or a tool name) deserves a human look before it is trusted. The
detector only *informs*: it returns flags that travel with the row (``flags`` JSON) for the UI. It never
blocks a write; the taint gate in ``memory_playbook`` decides status.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

# Manifest tools added by U26; listed here so flagging works before/without the spec table.
MEMORY_TOOL_NAMES = frozenset({
    "memory_view", "memory_create", "memory_str_replace", "memory_insert", "memory_delete",
})

_VERBS = (
    "always|never|ignore|disregard|forget|send|email|forward|share|post|upload|call|run|execute|delete|"
    "remove|reveal|print|leak|override|stop|ensure|remember|make sure|do not|don't|answer|reply|respond|"
    "write|open|visit|click|download|install|tell|ask|give|pass|exfiltrate|copy|paste|submit|approve"
)
_LEAD_IN = r"(?:(?:please|also|and|then|so|but|first|next)\s+)*"
_SENTENCE_START = re.compile(rf"^{_LEAD_IN}(?:{_VERBS})\b", re.IGNORECASE)
_ANYWHERE = re.compile(
    r"\b(?:from now on|ignore (?:all |any )?(?:the )?(?:previous|prior|above|earlier)|"
    r"you (?:must|should|shall|will|need to|are to|have to)|system prompt|new instructions?|"
    r"as an? (?:ai|assistant))\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"[.!?;\n]+")
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_TRAILING = ".,;:!?"


@lru_cache(maxsize=1)
def _tool_pattern() -> re.Pattern[str]:
    names = set(MEMORY_TOOL_NAMES)
    try:  # lazy: the spec table is the source of truth for tool names
        from app.internal_api.tool_specs import TOOL_SPECS

        names.update(TOOL_SPECS)
    except Exception:  # pragma: no cover - flagging must never fail a write
        pass
    # Only snake_case names: single plain words ("present_plan" yes, "task" no) would false-positive.
    ordered = sorted((n for n in names if "_" in n), key=len, reverse=True)
    return re.compile(r"(?<![A-Za-z0-9_])(?:" + "|".join(re.escape(n) for n in ordered) + r")(?![A-Za-z0-9_])")


def detect_flags(text: str) -> list[dict[str, Any]]:
    """Flags for ``text``: ``[{"kind": "imperative"|"url"|"email"|"tool_name", "match": str}]``."""
    if not isinstance(text, str) or not text.strip():
        return []
    flags: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def push(kind: str, match: str) -> None:
        key = (kind, match.lower())
        if key not in seen:
            seen.add(key)
            flags.append({"kind": kind, "match": match})

    for sentence in _SENTENCE_SPLIT.split(text):
        s = sentence.strip()
        if not s:
            continue
        m = _SENTENCE_START.match(s) or _ANYWHERE.search(s)
        if m:
            push("imperative", s[:80])
    for m in _URL.finditer(text):
        push("url", m.group(0).rstrip(_TRAILING))
    for m in _EMAIL.finditer(text):
        push("email", m.group(0).rstrip(_TRAILING))
    for m in _tool_pattern().finditer(text):
        push("tool_name", m.group(0))
    return flags


def flag_kinds(text: str) -> set[str]:
    return {f["kind"] for f in detect_flags(text)}
