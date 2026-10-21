"""Shrink a recorded trace response to fixture size without changing its structure.

A real trace carries whole system prompts and long model replies. For a fixture only the shape matters (entry kinds,
statuses, ids, usage), so every string above `max_chars` is clipped and says how much was cut.
"""

from __future__ import annotations

from typing import Any

DEFAULT_MAX_CHARS = 500


def _clip(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value
        return f"{value[:max_chars]}...[trimmed {len(value) - max_chars} chars]"
    if isinstance(value, dict):
        return {k: _clip(v, max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v, max_chars) for v in value]
    return value


def trim_trace(raw: dict[str, Any], max_chars: int = DEFAULT_MAX_CHARS) -> dict[str, Any]:
    """A copy of `raw` with long strings clipped."""
    return _clip(raw, max_chars)
