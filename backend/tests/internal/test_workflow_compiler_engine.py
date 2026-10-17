"""U15: the compiler emits exactly what the engine's strict graph schema accepts.

``assert_engine_valid`` replicates ``validateGraph`` (engine/src/workflow/graph.ts, run-task.ts
``parseRunInput``). The same compiled run inputs are committed as golden fixtures under
``tests/fixtures/workflow_graphs``; ``engine/test/workflow/backend-graphs.test.ts`` runs the REAL engine
validator over those files, so the two sides cannot drift apart. Regenerate with
``UPDATE_WORKFLOW_GRAPHS=1 uv run pytest tests/internal/test_workflow_compiler_engine.py``.
"""
import json
import os
import uuid
from pathlib import Path

import pytest

from app.services.workflow_compiler import (
    build_run_input,
    compile_for_run,
    compile_workflow,
    engine_graph,
)
from tests.internal.test_invoke import maker  # noqa: F401  (fixture)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "workflow_graphs"
RUN_ID = "00000000-0000-4000-8000-000000000001"
CHILD_ID = uuid.UUID("00000000-0000-4000-8000-0000000000c1")
NODE_KEYS = {"id", "type", "deps", "policy", "handles", "mayFanOut"}
POLICY_KEYS = {"maxRetries", "backoffMs", "timeoutMs", "onError"}
MAX_NODES, MAX_DEPTH = 95, 3


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "data": data}


def edge(src, dst, handle=None):
    e = {"id": f"{src}-{dst}", "source": src, "target": dst}
    if handle:
        e["sourceHandle"] = handle
    return e


def _count(raw):
    total = len(raw["nodes"])
    for key in ("subgraphs", "workflows"):
        total += sum(_count(g) for g in (raw.get(key) or {}).values())
    return total


def _check_level(graph, depth, ancestors):
    assert set(graph) <= {"nodes", "subgraphs", "workflows"}, f"extra graph keys {set(graph)}"
    assert graph["nodes"], "a graph needs at least one node"
    by_id = {}
    for n in graph["nodes"]:
        assert set(n) <= NODE_KEYS, f"extra node keys {set(n) - NODE_KEYS}"
        assert {"id", "type", "deps", "policy"} <= set(n)
        assert n["id"] and n["type"] and n["id"] not in by_id, "duplicate or empty id"
        assert set(n["policy"]) == POLICY_KEYS, "the policy is all four fields"
        p = n["policy"]
        assert all(isinstance(p[k], int) and not isinstance(p[k], bool) and p[k] >= 0
                   for k in ("maxRetries", "backoffMs", "timeoutMs"))
        assert p["onError"] in ("stop", "continue")
        assert "mayFanOut" not in n or isinstance(n["mayFanOut"], bool)
        by_id[n["id"]] = n
    for n in graph["nodes"]:
        for dep in n["deps"]:
            assert set(dep) <= {"from", "handle"}
            assert dep["from"] in by_id, f"{n['id']} depends on unknown {dep['from']}"
            if dep.get("handle") is not None:
                assert dep["handle"] in by_id[dep["from"]].get("handles", []), "undeclared handle"
    pending = {n["id"]: len(n["deps"]) for n in graph["nodes"]}
    queue = [i for i, c in pending.items() if c == 0]
    seen = 0
    while queue:
        cur = queue.pop()
        seen += 1
        for n in graph["nodes"]:
            for dep in n["deps"]:
                if dep["from"] == cur:
                    pending[n["id"]] -= 1
                    if pending[n["id"]] == 0:
                        queue.append(n["id"])
    assert seen == len(graph["nodes"]), "cycle"
    for sub in (graph.get("subgraphs") or {}).values():
        _check_level(sub, depth, ancestors)
    for wid, wf in (graph.get("workflows") or {}).items():
        assert wid not in ancestors, "recursive reference"
        assert depth + 1 <= MAX_DEPTH, "too deep"
        _check_level(wf, depth + 1, [*ancestors, wid])


def assert_engine_valid(run_input):
    assert set(run_input) <= {"version", "runId", "runAttempt", "retry", "deadlineMs", "graph", "variables", "errorMode"}
    assert run_input["version"] == 1 and run_input["runId"]
    assert isinstance(run_input["runAttempt"], int) and run_input["runAttempt"] >= 0
    assert run_input["errorMode"] in ("stop", "continue")
    assert _count(run_input["graph"]) <= MAX_NODES
    _check_level(run_input["graph"], 0, [])


def run_input_of(compiled):
    return build_run_input(RUN_ID, compiled)


LINEAR = {
    "nodes": [node("s", "start"), node("t", "template", template="x"), node("o", "output")],
    # React Flow's default source handle name on a non-branching node must not become a dep handle.
    "edges": [edge("s", "t", "source"), edge("t", "o")],
}
BRANCH = {
    "nodes": [node("s", "start"), node("c", "ifelse"), node("a", "template"), node("b", "template"),
              node("k", "classifier"), node("x", "template"), node("y", "template")],
    "edges": [edge("s", "c"), edge("c", "a", "true"), edge("c", "b", "false"), edge("a", "k"),
              edge("k", "x", "billing"), edge("k", "y", "tech")],
}
JOIN = {
    "nodes": [node("s", "start"), node("c", "ifelse"), node("a", "template"), node("b", "template"),
              node("j", "merge"), node("o", "output")],
    "edges": [edge("s", "c"), edge("c", "a", "true"), edge("c", "b", "false"), edge("a", "j"), edge("b", "j"),
              edge("j", "o")],
}
ITERATION = {
    "nodes": [node("s", "start"), node("it", "iteration", targetNodeId="t1", inputArray="items"),
              node("t1", "template"), node("t2", "template", adornments=[{"type": "retry", "maxRetries": 2}]),
              node("after", "output")],
    "edges": [edge("s", "it"), edge("it", "t1"), edge("t1", "t2"), edge("t2", "it", "iteration_output"),
              edge("it", "after")],
}
SUBWORKFLOW_PARENT = {
    "nodes": [node("s", "start"), node("sub", "subworkflow", workflowId=str(CHILD_ID)), node("o", "output")],
    "edges": [edge("s", "sub"), edge("sub", "o")],
}
SUBWORKFLOW_CHILD = {"nodes": [node("cs", "start"), node("ct", "template", template="child")],
                     "edges": [edge("cs", "ct")]}


@pytest.mark.parametrize("name,spec", [("linear", LINEAR), ("branch", BRANCH), ("join", JOIN), ("iteration", ITERATION)])
def test_representative_workflows_compile_to_the_engine_schema(name, spec):
    run_input = run_input_of(compile_workflow(spec))
    assert_engine_valid(run_input)
    json.dumps(run_input)  # JSON all the way down
    check_golden(name, run_input)


def check_golden(name, run_input):
    path = FIXTURE_DIR / f"{name}.json"
    text = json.dumps(run_input, indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_WORKFLOW_GRAPHS"):
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    assert path.exists(), f"missing golden {path.name}: run with UPDATE_WORKFLOW_GRAPHS=1"
    assert json.loads(path.read_text()) == json.loads(text), f"{path.name} is stale: UPDATE_WORKFLOW_GRAPHS=1"


def test_branch_nodes_declare_handles_and_other_nodes_carry_no_handle_deps():
    graph = engine_graph(compile_workflow(BRANCH))
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert nodes["c"]["handles"] == ["true", "false"] and nodes["k"]["handles"] == ["billing", "tech"]
    assert "handles" not in nodes["a"]
    assert nodes["a"]["deps"] == [{"from": "c", "handle": "true"}]
    linear = {n["id"]: n for n in engine_graph(compile_workflow(LINEAR))["nodes"]}
    assert linear["t"]["deps"] == [{"from": "s", "handle": None}]


def test_fanout_nodes_carry_may_fan_out_and_never_retry():
    compiled = compile_workflow(ITERATION)
    nodes = {n["id"]: n for n in engine_graph(compiled)["nodes"]}
    assert nodes["it"]["mayFanOut"] is True
    assert "mayFanOut" not in nodes["after"] and "mayFanOut" not in nodes["s"]
    assert nodes["it"]["policy"]["maxRetries"] == 0
    body = engine_graph(compiled)["subgraphs"]["it:sub"]
    assert [n["id"] for n in body["nodes"]] == ["t1", "t2"]
    assert {n["id"]: n for n in body["nodes"]}["t2"]["policy"]["maxRetries"] == 2


def test_engine_graph_strips_everything_the_strict_schema_refuses():
    spec = {"nodes": [node("s", "start"), node("d", "data")], "edges": [edge("s", "d")]}
    compiled = compile_workflow(spec)
    assert compiled["warnings"]
    graph = engine_graph(compiled)
    assert set(graph) == {"nodes"}
    assert_engine_valid(run_input_of(compiled))


def test_try_adornment_sets_error_mode_in_the_run_input():
    spec = {"nodes": [node("a", "template", adornments=[{"type": "try"}])], "edges": []}
    run_input = run_input_of(compile_workflow(spec))
    assert run_input["errorMode"] == "continue" and run_input["graph"]["nodes"][0]["policy"]["onError"] == "continue"
    assert_engine_valid(run_input)


def test_retry_and_deadline_are_optional_run_input_fields():
    compiled = compile_workflow(LINEAR)
    body = build_run_input(RUN_ID, compiled, retry={"max": 2, "delayMs": 60000}, deadline_ms=123)
    assert body["retry"] == {"max": 2, "delayMs": 60000} and body["deadlineMs"] == 123
    assert_engine_valid(body)
    assert "retry" not in run_input_of(compiled) and "deadlineMs" not in run_input_of(compiled)


async def _seed(maker, specs):
    from app.models.tenant import Tenant
    from app.models.user import User
    from app.models.workflow import Workflow

    tenant, user = uuid.uuid4(), uuid.uuid4()
    async with maker() as db:
        db.add(Tenant(id=tenant, name="t", slug=f"t-{tenant.hex[:6]}", is_active=True, settings={}))
        await db.commit()
        db.add(User(id=user, tenant_id=tenant, email="u@x.io", name="u"))
        await db.commit()
        for wid, spec in specs.items():
            db.add(Workflow(id=wid, user_id=user, tenant_id=tenant, name=str(wid)[-4:], spec=spec))
        await db.commit()
    return tenant


async def test_subworkflow_compiles_with_the_referenced_workflow_in_the_engine_shape(maker):
    tenant = await _seed(maker, {CHILD_ID: SUBWORKFLOW_CHILD})
    async with maker() as db:
        compiled = await compile_for_run(db, tenant, SUBWORKFLOW_PARENT)
    run_input = run_input_of(compiled)
    assert_engine_valid(run_input)
    graph = run_input["graph"]
    assert [n["id"] for n in graph["workflows"][str(CHILD_ID)]["nodes"]] == ["cs", "ct"]
    assert {n["id"]: n for n in graph["nodes"]}["sub"]["mayFanOut"] is True
    check_golden("subworkflow", run_input)


async def test_subworkflow_inside_an_iteration_body_is_attached_to_the_body_subgraph(maker):
    tenant = await _seed(maker, {CHILD_ID: SUBWORKFLOW_CHILD})
    spec = {
        "nodes": [node("it", "iteration", targetNodeId="sub", inputArray="items"),
                  node("sub", "subworkflow", workflowId=str(CHILD_ID))],
        "edges": [edge("it", "sub"), edge("sub", "it", "iteration_output")],
    }
    async with maker() as db:
        compiled = await compile_for_run(db, tenant, spec)
    graph = engine_graph(compiled)
    assert "workflows" not in graph  # the lane resolves `{workflowId}` against its own graph
    assert str(CHILD_ID) in graph["subgraphs"]["it:sub"]["workflows"]
    assert_engine_valid(run_input_of(compiled))


async def test_self_referencing_missing_and_too_deep_subworkflows_are_refused(maker):
    other = uuid.UUID("00000000-0000-4000-8000-0000000000c2")
    deeper = [uuid.UUID(f"00000000-0000-4000-8000-0000000000d{i}") for i in range(4)]
    chain = {deeper[i]: {"nodes": [node(f"n{i}", "subworkflow", workflowId=str(deeper[i + 1]))], "edges": []}
             for i in range(3)}
    chain[deeper[3]] = SUBWORKFLOW_CHILD
    tenant = await _seed(maker, {CHILD_ID: {"nodes": [node("x", "subworkflow", workflowId=str(CHILD_ID))], "edges": []},
                                 **chain})
    async with maker() as db:
        with pytest.raises(ValueError, match="references itself"):
            await compile_for_run(db, tenant, SUBWORKFLOW_PARENT)
        missing = {"nodes": [node("m", "subworkflow", workflowId=str(other))], "edges": []}
        with pytest.raises(ValueError, match="not found"):
            await compile_for_run(db, tenant, missing)
        deep = {"nodes": [node("d", "subworkflow", workflowId=str(deeper[0]))], "edges": []}
        with pytest.raises(ValueError, match="nest at most"):
            await compile_for_run(db, tenant, deep)
        # A workflow can also refuse to contain itself directly.
        direct = {"nodes": [node("z", "subworkflow", workflowId=str(CHILD_ID))], "edges": []}
        with pytest.raises(ValueError, match="references itself"):
            await compile_for_run(db, tenant, direct, workflow_id=CHILD_ID)
