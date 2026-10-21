from pathlib import Path

import pytest

from evals.loader import TaskLoadError, load_task, load_tasks, select_by_tags
from evals.schema import GraderSpec, Span, Task, Trace

MINIMAL = """
id: {id}
kind: chat
tags: {tags}
input:
  turns: ["hello"]
graders:
  - id: g1
    type: budget
    gating: true
    params: {{max_usd: 0.5}}
"""


def write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body)
    return p


def test_valid_task_loads_with_defaults(tmp_path):
    p = write(tmp_path, "a.yaml", MINIMAL.format(id="t-a", tags="[smoke]"))
    t = load_task(p)
    assert isinstance(t, Task)
    assert t.id == "t-a" and t.kind == "chat" and t.tags == ("smoke",)
    assert t.environment.web == "replay"
    assert t.graders == (GraderSpec(id="g1", type="budget", gating=True, params={"max_usd": 0.5}),)
    assert t.budget.max_usd is None


def test_missing_id_fails(tmp_path):
    p = write(tmp_path, "a.yaml", "kind: chat\ninput: {}\n")
    with pytest.raises(TaskLoadError, match="id"):
        load_task(p)


def test_unknown_grader_type_fails(tmp_path):
    body = MINIMAL.format(id="t-a", tags="[]").replace("type: budget", "type: nope")
    with pytest.raises(TaskLoadError, match="nope"):
        load_task(write(tmp_path, "a.yaml", body))


def test_unknown_kind_and_tag_fail(tmp_path):
    with pytest.raises(TaskLoadError, match="kind"):
        load_task(write(tmp_path, "a.yaml", MINIMAL.format(id="x", tags="[]").replace("chat", "weird")))
    with pytest.raises(TaskLoadError, match="tag"):
        load_task(write(tmp_path, "b.yaml", MINIMAL.format(id="x", tags="[bogus]")))


def test_bad_web_mode_fails(tmp_path):
    body = MINIMAL.format(id="x", tags="[]") + "environment:\n  web: live\n"
    with pytest.raises(TaskLoadError, match="web"):
        load_task(write(tmp_path, "a.yaml", body))


def test_duplicate_ids_fail(tmp_path):
    write(tmp_path, "a.yaml", MINIMAL.format(id="dup", tags="[]"))
    sub = tmp_path / "sub"
    sub.mkdir()
    write(sub, "b.yaml", MINIMAL.format(id="dup", tags="[]"))
    with pytest.raises(TaskLoadError, match="duplicate"):
        load_tasks(tmp_path)


def test_duplicate_grader_ids_fail(tmp_path):
    body = MINIMAL.format(id="x", tags="[]") + "  - id: g1\n    type: rounds\n"
    with pytest.raises(TaskLoadError, match="g1"):
        load_task(write(tmp_path, "a.yaml", body))


def test_tag_selection_exact_and_stable(tmp_path):
    write(tmp_path, "b.yaml", MINIMAL.format(id="t-b", tags="[smoke, regression]"))
    write(tmp_path, "a.yaml", MINIMAL.format(id="t-a", tags="[regression]"))
    write(tmp_path, "c.yaml", MINIMAL.format(id="t-c", tags="[smoke]"))
    tasks = load_tasks(tmp_path)
    assert [t.id for t in tasks] == ["t-a", "t-b", "t-c"]
    assert [t.id for t in select_by_tags(tasks, ["smoke"])] == ["t-b", "t-c"]
    assert [t.id for t in select_by_tags(tasks, ["regression"])] == ["t-a", "t-b"]
    assert [t.id for t in select_by_tags(tasks, [])] == ["t-a", "t-b", "t-c"]
    assert select_by_tags(tasks, ["memory"]) == []


def test_trace_tree_walk():
    leaf = Span(kind="tool", name="web_search", status="ok")
    child = Span(kind="subagent", name="worker", status="ok", children=(leaf,))
    root = Span(kind="model", name="root", status="ok", children=(child,))
    trace = Trace(root=root)
    assert [s.name for s in trace.walk()] == ["root", "worker", "web_search"]
    assert trace.taint is False and trace.compactions == ()
