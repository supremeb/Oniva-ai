"""U13 / KTD5: the workflow compiler (spec -> compiled graph)."""
import uuid

import pytest

from app.services.workflow_compiler import compile_workflow, resolve_spec, untaken_edges


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "data": data}


def edge(src, dst, handle=None):
    e = {"id": f"{src}-{dst}", "source": src, "target": dst}
    if handle:
        e["sourceHandle"] = handle
    return e


def by_id(compiled):
    return {n["id"]: n for n in compiled["nodes"]}


def test_condition_and_classifier_expose_handles_and_untaken_edges_are_marked():
    spec = {
        "nodes": [node("s", "start"), node("c", "ifelse"), node("a", "template"), node("b", "template"),
                  node("k", "classifier"), node("x", "template")],
        "edges": [edge("s", "c"), edge("c", "a", "true"), edge("c", "b", "false"), edge("a", "k"), edge("k", "x", "cls1")],
    }
    compiled = compile_workflow(spec)
    nodes = by_id(compiled)
    assert nodes["c"]["handles"] == ["true", "false"]
    assert nodes["k"]["handles"] == ["cls1"]
    assert "handles" not in nodes["a"]
    assert nodes["a"]["deps"] == [{"from": "c", "handle": "true"}]
    assert untaken_edges(compiled, "c", ["true"]) == [{"from": "c", "to": "b", "handle": "false"}]
    assert untaken_edges(compiled, "c", None) == []
    assert compiled["errorMode"] == "stop"


def test_cycles_are_rejected_with_the_validators_error():
    spec = {"nodes": [node("a", "template"), node("b", "template")], "edges": [edge("a", "b"), edge("b", "a")]}
    with pytest.raises(ValueError, match="circular dependency"):
        compile_workflow(spec)


def test_unsupported_node_type_is_rejected_with_the_validators_error():
    with pytest.raises(ValueError, match="Unrecognized node types"):
        compile_workflow({"nodes": [node("a", "teleport")], "edges": []})


def test_iteration_target_reachable_set_leaves_the_main_graph_and_attaches_to_the_fanout():
    spec = {
        "nodes": [node("s", "start"), node("it", "iteration", targetNodeId="t1", inputArray="items"),
                  node("t1", "template"), node("t2", "template"), node("after", "output")],
        "edges": [edge("s", "it"), edge("it", "t1"), edge("t1", "t2"), edge("t2", "it", "iteration_output"),
                  edge("it", "after")],
    }
    compiled = compile_workflow(spec)
    nodes = by_id(compiled)
    assert set(nodes) == {"s", "it", "after"}
    assert nodes["it"]["fanout"] == {"subgraph": "it:sub", "entry": "t1", "nodes": ["t1", "t2"]}
    assert nodes["after"]["deps"] == [{"from": "it", "handle": None}]
    assert nodes["it"]["policy"]["maxRetries"] == 0


def test_adornments_map_to_policy_and_fanout_nodes_never_retry():
    spec = {
        "nodes": [
            node("r", "template", adornments=[{"type": "retry", "maxRetries": 2, "backoffSeconds": 1.5},
                                                {"type": "timeout", "timeoutSeconds": 9}, {"type": "cache"}]),
            node("t", "template", adornments=[{"type": "try"}]),
            node("it", "iteration", targetNodeId="x", adornments=[{"type": "retry", "maxRetries": 5}]),
            node("x", "template"),
        ],
        "edges": [edge("it", "x")],
    }
    compiled = compile_workflow(spec)
    nodes = by_id(compiled)
    assert nodes["r"]["policy"] == {"maxRetries": 2, "backoffMs": 1500, "timeoutMs": 9000}
    assert nodes["t"]["policy"] == {"maxRetries": 0, "onError": "continue"}
    assert nodes["it"]["policy"] == {"maxRetries": 0}
    assert compiled["errorMode"] == "continue"


def test_builder_types_without_executors_compile_as_skipped_with_a_warning():
    spec = {"nodes": [node("s", "start"), node("d", "data"), node("r", "research")], "edges": [edge("s", "d")]}
    compiled = compile_workflow(spec)
    nodes = by_id(compiled)
    assert nodes["d"]["skip"] is True and "no executor" in nodes["d"]["warning"]
    assert {w["nodeId"] for w in compiled["warnings"]} == {"d", "r"}
    assert "skip" not in nodes["s"]


def test_node_limit_comes_from_the_validator():
    spec = {"nodes": [node(f"n{i}", "template") for i in range(96)], "edges": []}
    with pytest.raises(ValueError, match="maximum node count"):
        compile_workflow(spec)


async def test_pinned_version_resolves_its_frozen_spec(maker):
    from app.models.tenant import Tenant
    from app.models.user import User
    from app.models.workflow import Workflow, WorkflowVersion

    tenant, user, wf, ver = (uuid.uuid4() for _ in range(4))
    async with maker() as db:
        db.add(Tenant(id=tenant, name="t", slug=f"t-{tenant.hex[:6]}", is_active=True, settings={}))
        await db.commit()
        db.add(User(id=user, tenant_id=tenant, email="u@x.io", name="u"))
        await db.commit()
        db.add(Workflow(id=wf, user_id=user, tenant_id=tenant, name="w", spec={"nodes": [{"id": "live"}], "edges": []}))
        await db.commit()
        db.add(WorkflowVersion(id=ver, workflow_id=wf, tenant_id=tenant, version_number=1,
                               spec={"nodes": [{"id": "pinned"}], "edges": []}))
        await db.commit()
        assert (await resolve_spec(db, tenant, wf, ver))["nodes"][0]["id"] == "pinned"
        assert (await resolve_spec(db, tenant, wf))["nodes"][0]["id"] == "live"
        assert await resolve_spec(db, uuid.uuid4(), wf, ver) is None


from tests.internal.test_invoke import maker  # noqa: E402,F401  (fixture)
