import json
from pathlib import Path

import pytest

from evals.graders import GradeContext, grade
from evals.graders.trajectory import match_trajectory, tool_calls
from evals.schema import GraderSpec, Span, Trace
from evals.trace import flatten

FIX = Path(__file__).parent / "fixtures"


def trace_of(*names: str, args: dict[str, str] | None = None) -> Trace:
    spans = tuple(Span(kind="tool", name=n, args_hash=(args or {}).get(n)) for n in names)
    return Trace(root=Span(kind="subagent", name="root", children=(Span(kind="model", name="m"), *spans)))


ACTUAL = ["a", "b", "c"]


@pytest.mark.parametrize(
    "mode,expected,ok",
    [
        ("exact", ["a", "b", "c"], True),
        ("exact", ["a", "b"], False),  # extra call
        ("exact", ["a", "c", "b"], False),  # swapped
        ("ordered", ["a", "c"], True),  # extras tolerated, order kept
        ("ordered", ["c", "a"], False),  # swapped pair
        ("superset", ["c", "a"], True),  # any order, extras fine
        ("superset", ["a", "d"], False),  # missing call
        ("subset", ["c", "b", "a", "z"], True),  # nothing outside expected
        ("subset", ["a", "b"], False),  # unexpected extra call
    ],
)
def test_modes(mode, expected, ok):
    out = match_trajectory(tool_calls(trace_of(*ACTUAL)), expected, mode)
    assert out["passed"] is ok, out


def test_unknown_mode():
    with pytest.raises(ValueError):
        match_trajectory([], [], "fuzzy")


def test_superset_respects_multiplicity():
    assert match_trajectory(tool_calls(trace_of("a")), ["a", "a"], "superset")["passed"] is False
    assert match_trajectory(tool_calls(trace_of("a", "a")), ["a", "a"], "superset")["passed"] is True


def test_args_predicate():
    calls = tool_calls(trace_of("a", "b", args={"a": "h1", "b": "h2"}))
    assert match_trajectory(calls, [{"name": "a", "args_hash": "h1"}, "b"], "exact")["passed"] is True
    assert match_trajectory(calls, [{"name": "a", "args_hash": "zz"}, "b"], "exact")["passed"] is False
    out = match_trajectory(calls, ["a", "b"], "exact", args_match=lambda span, want: span.args_hash == "h1" if span.name == "a" else True)
    assert out["passed"] is True


def test_scope_root_vs_all():
    trace = flatten(json.loads((FIX / "trace_deep_work.json").read_text()))
    assert [s.name for s in tool_calls(trace)] == ["dw_set_plan", "research_worker", "research_worker", "reflector", "dw_finalize"]
    assert "web_search" in [s.name for s in tool_calls(trace, scope="all")]


def test_trajectory_match_grader_dispatch():
    trace = trace_of("a", "b")
    spec = GraderSpec(id="t", type="trajectory_match", params={"mode": "ordered", "expected": ["a", "b"]})
    out = grade(spec, GradeContext(trace=trace))
    assert out == {"id": "t", "passed": True, "score": 1.0, "detail": out["detail"]}
    bad = GraderSpec(id="t", type="trajectory_match", params={"mode": "exact", "expected": ["b", "a"]})
    assert grade(bad, GradeContext(trace=trace))["passed"] is False


def test_grader_requires_expected():
    with pytest.raises(ValueError):
        grade(GraderSpec(id="t", type="trajectory_match", params={"mode": "exact"}), GradeContext(trace=trace_of("a")))
