"""Compare the ordered tool-call names of a run to an expected list.

Modes:
- `exact`: same calls, same order, nothing extra.
- `ordered`: every expected call appears in order; extra calls may sit between.
- `superset`: every expected call appears (any order, with multiplicity); extras allowed.
- `subset`: no call outside the expected set (a call may be repeated or omitted).

An expected item is a name or `{name, args_hash}`; `args_match(span, want)`
overrides how an item matches a span (Python callers only).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

from evals.schema import Span, Trace

from . import GradeContext, Outcome, outcome

MODES = ("exact", "ordered", "superset", "subset")
Expected = str | dict[str, Any]
ArgsMatch = Callable[[Span, Expected], bool]


def tool_calls(trace: Trace, scope: str = "root") -> list[Span]:
    """Tool and subagent calls in recorded order. `root`: the root conversation only."""
    if scope == "all":
        return [s for s in trace.walk() if s is not trace.root and s.kind in ("tool", "subagent")]
    return [s for s in trace.root.children if s.kind in ("tool", "subagent")]


def _name(want: Expected) -> str:
    return want if isinstance(want, str) else str(want.get("name"))


def _default_match(span: Span, want: Expected) -> bool:
    if span.name != _name(want):
        return False
    return isinstance(want, str) or want.get("args_hash") in (None, span.args_hash)


def match_trajectory(
    actual: Sequence[Span],
    expected: Sequence[Expected],
    mode: str,
    args_match: ArgsMatch | None = None,
) -> Outcome:
    if mode not in MODES:
        raise ValueError(f"unknown trajectory mode '{mode}' (known: {', '.join(MODES)})")
    match = args_match or _default_match
    names = [s.name for s in actual]
    want_names = [_name(w) for w in expected]
    shown = f"actual {names} vs expected {want_names} ({mode})"

    if mode == "exact":
        ok = len(actual) == len(expected) and all(match(s, w) for s, w in zip(actual, expected, strict=True))
    elif mode == "ordered":
        it = iter(actual)
        ok = all(any(match(s, w) for s in it) for w in expected)
    elif mode == "superset":
        remaining = list(actual)
        ok = True
        for w in expected:
            idx = next((i for i, s in enumerate(remaining) if match(s, w)), None)
            if idx is None:
                ok = False
                break
            remaining.pop(idx)
    else:  # subset
        ok = all(any(match(s, w) for w in expected) for s in actual)

    if ok:
        return outcome(True, shown)
    extra = sorted((Counter(names) - Counter(want_names)).elements())
    missing = sorted((Counter(want_names) - Counter(names)).elements())
    return outcome(False, f"{shown}; extra {extra}, missing {missing}")


def trajectory_match(ctx: GradeContext, params: dict[str, Any]) -> Outcome:
    expected = params.get("expected")
    if not isinstance(expected, list):
        raise ValueError("trajectory_match needs 'expected' (a list of tool names)")
    return match_trajectory(
        tool_calls(ctx.trace, str(params.get("scope", "root"))), expected, str(params.get("mode", "exact"))
    )
