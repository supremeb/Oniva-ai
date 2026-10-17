"""U13 / KTD4: the idempotent backend node gateway under /internal/workflows.

The contract is the U33 schemas and fixtures (engine/contracts/workflow-nodes) and the clauses of the
fake workflow backend (U11, `engine/test/support/fake-workflow-backend.ts`), whose `@wf_clause` tests live
in test_contract_conformance.py.
"""
import json
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.config import Settings
from app.internal_api import workflow_contract as wc
from app.internal_api import workflow_nodes
from app.internal_api.invocations import get_session_factory
from app.internal_api.router import mount_internal_router
from app.models.engine import EngineConversation, ToolInvocation
from app.models.project import Project
from app.models.tenant import Tenant
from app.models.user import User
from app.models.workflow import Workflow, WorkflowExecution
from app.node_executors.adornments import CacheAdornment
from app.node_executors.http_executor import HTTPExecutor
from app.node_executors.template_executor import TemplateExecutor
from tests.internal.test_invoke import AUTH, ENGINE_SVC, GATEWAY, SECRET, factory, maker  # noqa: F401  (fixtures)

FIXTURES = Path(__file__).resolve().parents[3] / "engine/contracts/workflow-nodes/fixtures"
SESSION = "wf-sess"


def n(nid, ntype, **data):
    return {"id": nid, "type": ntype, "data": data}


def e(src, dst, handle=None):
    d = {"id": f"{src}-{dst}", "source": src, "target": dst}
    if handle:
        d["sourceHandle"] = handle
    return d


SPEC = {
    "nodes": [
        n("s", "start"),
        n("t", "template", template="Hello {{name}}"),
        n("h", "http", url="http://example.invalid", method="GET"),
        n("cached", "template", template="c {{name}}", adornments=[{"type": "cache"}]),
        n("it", "iteration", targetNodeId="sub", inputArray="items", parallel=True, maxParallel=4),
        n("sub", "template", template="{{it.item}}"),
        n("d", "data"),
        n("bad", "template", template="x"),
    ],
    "edges": [e("s", "t"), e("t", "h"), e("it", "sub")],
}


class World:
    pass


@pytest.fixture
def counts(monkeypatch):
    c = {"template": 0, "http": 0, "fail_first": False}
    real = TemplateExecutor.execute

    async def execute(self, context, db):
        c["template"] += 1
        if c["fail_first"] and context.node_id == "bad" and c["template"] == 1:
            raise RuntimeError("boom")
        return await real(self, context, db)

    monkeypatch.setattr(TemplateExecutor, "execute", execute)

    async def http(self, context, db):
        c["http"] += 1
        raise AssertionError("an export-blocked node must not run")

    monkeypatch.setattr(HTTPExecutor, "execute", http)
    audit = []

    async def log(self, context, classification):
        audit.append((context.node_id, classification.value))

    monkeypatch.setattr(HTTPExecutor, "_log_export_violation_async", log)
    c["audit"] = audit
    CacheAdornment._cache.clear()
    return c


@pytest.fixture
async def wf(maker, factory, counts):
    app = FastAPI()
    mount_internal_router(
        app, Settings(_env_file=None, SECRET_KEY=SECRET, ENGINE_GATEWAY_TOKEN=GATEWAY, ENGINE_SERVICE_TOKEN=ENGINE_SVC)
    )
    app.dependency_overrides[get_session_factory] = lambda: factory
    app.dependency_overrides[workflow_nodes.get_tenant_session_factory] = lambda: (lambda tid: factory())

    w = World()
    w.maker, w.counts = maker, counts
    w.tenant_a, w.tenant_b = uuid.uuid4(), uuid.uuid4()
    w.user_a, w.user_b = uuid.uuid4(), uuid.uuid4()
    w.project_a, w.project_b = uuid.uuid4(), uuid.uuid4()
    w.wf_id, w.run = uuid.uuid4(), uuid.uuid4()
    w.conv_a, w.conv_b = uuid.uuid4(), uuid.uuid4()
    w.items = [f"i{i}" for i in range(3)]
    async with maker() as db:
        for t, tag in ((w.tenant_a, "a"), (w.tenant_b, "b")):
            db.add(Tenant(id=t, name=tag, slug=f"t-{tag}-{t.hex[:6]}", is_active=True, settings={}))
        await db.commit()
        db.add(User(id=w.user_a, tenant_id=w.tenant_a, email="a@x.io", name="a"))
        db.add(User(id=w.user_b, tenant_id=w.tenant_b, email="b@x.io", name="b"))
        await db.commit()
        db.add(Project(id=w.project_a, tenant_id=w.tenant_a, name="a", owner_id=w.user_a))
        db.add(Project(id=w.project_b, tenant_id=w.tenant_b, name="b", owner_id=w.user_b))
        await db.commit()
        db.add(Workflow(id=w.wf_id, user_id=w.user_a, tenant_id=w.tenant_a, name="w", spec=SPEC))
        await db.commit()
        db.add(WorkflowExecution(id=w.run, workflow_id=w.wf_id, user_id=w.user_a, tenant_id=w.tenant_a,
                                 status="running", input_data={"name": "Ada", "items": w.items}, execution_trace=[]))
        await db.commit()
        db.add(EngineConversation(conversation_id=w.conv_a, tenant_id=w.tenant_a, project_id=w.project_a,
                                  user_id=w.user_a, session_id=SESSION, run_kind="workflow", run_ref_id=w.run))
        db.add(EngineConversation(conversation_id=w.conv_b, tenant_id=w.tenant_b, project_id=w.project_b,
                                  user_id=w.user_b, session_id="other"))
        await db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        w.client = client
        yield w
    from app.internal_api import tools
    import asyncio

    pending = list(tools._effects)
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 10)


def scope(w, **extra):
    return {"tenantId": str(w.tenant_a), "projectId": str(w.project_a), "conversationId": str(w.conv_a), **extra}


async def call(w, node, phase="prepare", *, r=0, a=0, body=None, key=None, token=None, run=None, **extra):
    run = run or str(w.run)
    headers = {**AUTH, "Idempotency-Key": key if key is not None else f"{run}:{node}:r{r}:a{a}:{phase}",
               "X-Engine-Session": SESSION}
    if token is not None:
        headers["X-Engine-Fence"] = str(token)
    payload = body if body is not None else {**scope(w), "contractVersion": 1, **extra}
    return await w.client.post(f"/internal/workflows/runs/{run}/nodes/{node}/{phase}", json=payload, headers=headers)


async def trace(w):
    async with w.maker() as db:
        return (await db.execute(select(WorkflowExecution.execution_trace).where(WorkflowExecution.id == w.run))).scalar_one()


async def children(w):
    async with w.maker() as db:
        return (await db.execute(select(func.count()).select_from(WorkflowExecution).where(WorkflowExecution.id != w.run))).scalar_one()


# ---- prepare: final -------------------------------------------------------------------------


async def test_template_prepare_returns_final_and_a_repeated_key_does_not_rerun(wf):
    r1 = await call(wf, "t")
    r2 = await call(wf, "t")
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    body = wc.validate_prepare_response(r1.json())
    assert body.kind == "final" and body.status == "success"
    assert body.output["output"] == "Hello Ada"
    assert wf.counts["template"] == 1
    assert r1.headers["cache-control"] == "no-store"
    async with wf.maker() as db:
        [row] = (await db.execute(select(ToolInvocation))).scalars().all()
    assert row.status == "completed" and row.idempotency_key == f"{wf.run}:t:r0:a0:prepare"


async def test_cross_tenant_tenant_id_is_404(wf):
    r = await call(wf, "t", body={"tenantId": str(wf.tenant_b), "projectId": str(wf.project_b),
                                  "conversationId": str(wf.conv_b), "contractVersion": 1})
    assert r.status_code == 404
    assert wf.counts["template"] == 0
    r = await call(wf, "t", body={"tenantId": str(wf.tenant_b), "projectId": str(wf.project_a), "contractVersion": 1})
    assert r.status_code == 404


async def test_a_conversation_of_another_run_is_404(wf):
    async with wf.maker() as db:
        conv = await db.get(EngineConversation, wf.conv_a)
        conv.run_ref_id = uuid.uuid4()
        await db.commit()
    assert (await call(wf, "t")).status_code == 404


async def test_unknown_run_and_node_are_404(wf):
    other = str(uuid.uuid4())
    assert (await call(wf, "t", run=other)).status_code == 404
    assert (await call(wf, "ghost")).status_code == 404


async def test_fence_rejected_is_409_and_runs_nothing(wf):
    assert (await call(wf, "t", token=7)).status_code == 200
    r = await call(wf, "cached", token=6)
    assert r.status_code == 409
    assert r.json() == {"error": "fence_rejected", "highWater": 7}
    assert [s["node_id"] for s in await trace(wf)] == ["t"]


async def test_confidential_input_blocks_an_external_calling_node_and_audits(wf):
    r = await call(wf, "h", classification="confidential")
    body = wc.validate_prepare_response(r.json())
    assert body.status == "blocked" and body.classification == "confidential"
    assert "Export Control" in body.error
    assert wf.counts["audit"] == [("h", "confidential")]
    assert wf.counts["http"] == 0


async def test_classification_high_water_mark_is_returned(wf):
    r = await call(wf, "t", classification="confidential")
    assert r.json()["classification"] == "confidential"
    assert (await call(wf, "t", classification="secret", a=1)).status_code == 400


# ---- keys -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "{run}:t:a0:prepare",
        "{run}:t:r0:a0:generate",
        "{run}:t:r0:prepare",
        "{run}:other:r0:a0:prepare",
        "{run}:t:r0:a0:finish",
        "garbage",
    ],
)
async def test_malformed_or_mismatched_key_is_400(wf, key):
    r = await call(wf, "t", key=key.format(run=wf.run))
    assert r.status_code == 400
    assert wf.counts["template"] == 0


async def test_missing_key_is_400(wf):
    r = await wf.client.post(f"/internal/workflows/runs/{wf.run}/nodes/t/prepare", json=scope(wf), headers=AUTH)
    assert r.status_code == 400


async def test_the_same_node_under_r0_and_r1_runs_twice_and_stores_two_outcomes(wf):
    assert (await call(wf, "t", r=0)).status_code == 200
    assert (await call(wf, "t", r=1)).status_code == 200
    assert wf.counts["template"] == 2
    async with wf.maker() as db:
        keys = sorted(k for (k,) in (await db.execute(select(ToolInvocation.idempotency_key))).all())
    assert keys == [f"{wf.run}:t:r0:a0:prepare", f"{wf.run}:t:r1:a0:prepare"]
    steps = await trace(wf)
    assert sorted((s["run_attempt"], s["attempt"]) for s in steps) == [(0, 0), (1, 0)]


async def test_a_failed_r0_outcome_is_replayed_but_r1_re_executes_and_succeeds(wf):
    wf.counts["fail_first"] = True
    first = (await call(wf, "bad", r=0)).json()
    assert first["status"] == "failed" and "boom" in first["error"]
    assert (await call(wf, "bad", r=0)).json() == first
    assert wf.counts["template"] == 1
    second = (await call(wf, "bad", r=1)).json()
    assert second["status"] == "success"
    assert wf.counts["template"] == 2


async def test_same_key_with_a_different_body_is_422(wf):
    assert (await call(wf, "t")).status_code == 200
    r = await call(wf, "t", classification="public")
    assert r.status_code == 422 and r.json() == {"error": "idempotency_key_reuse"}


# ---- adornments -----------------------------------------------------------------------------


async def test_cache_adornment_still_hits_across_keys(wf):
    first = (await call(wf, "cached", a=0)).json()
    second = (await call(wf, "cached", a=1)).json()
    assert first["output"]["output"] == "c Ada"
    assert wf.counts["template"] == 1
    assert second["output"]["_cached"] is True


async def test_unexecuted_builder_types_are_skipped(wf):
    body = (await call(wf, "d")).json()
    assert body["status"] == "skipped" and "no executor" in body["output"]["message"]


# ---- trace ----------------------------------------------------------------------------------


async def test_every_final_writes_one_trace_step_and_a_replay_does_not_duplicate_it(wf):
    await call(wf, "t")
    await call(wf, "t")
    [step] = await trace(wf)
    assert step["node_id"] == "t" and step["status"] == "success" and step["output"]["output"] == "Hello Ada"
    async with wf.maker() as db:
        row = await db.get(WorkflowExecution, wf.run)
    assert row.nodes_executed == 1


async def test_predecessor_outputs_come_from_the_stored_trace(wf):
    await call(wf, "t")
    [step] = await trace(wf)
    assert step["output"]["output"] == "Hello Ada"
    # `h` depends on `t`; export control blocks it for confidential data, success otherwise is not run offline.
    blocked = (await call(wf, "h", classification="confidential")).json()
    assert blocked["status"] == "blocked"


# ---- fan-out --------------------------------------------------------------------------------


async def set_items(w, items):
    async with w.maker() as db:
        row = await db.get(WorkflowExecution, w.run)
        row.input_data = {**row.input_data, "items": items}
        await db.commit()


async def test_fanout_prepare_creates_item_rows_once_and_returns_no_payloads(wf):
    await set_items(wf, list(range(1000)))
    r = await call(wf, "it")
    assert r.status_code == 200
    body = wc.validate_prepare_response(r.json())
    assert body.kind == "fanout" and body.count == 1000 and body.parallel == 4 and body.failFast is True
    assert body.target.subgraph == "it:sub" and body.itemsRef == f"run:{wf.run}:it"
    assert len(r.content) < wc.MAX_BODY_BYTES
    assert await children(wf) == 1000
    async with wf.maker() as db:
        ids = {i for (i,) in (await db.execute(select(WorkflowExecution.id).where(WorkflowExecution.id != wf.run))).all()}
    assert ids == {uuid.uuid5(wf.run, f"it:{i}") for i in range(1000)}
    # A new key for the same node (node retry) finds the rows instead of creating more.
    assert (await call(wf, "it", a=1)).status_code == 200
    assert await children(wf) == 1000
    assert (await call(wf, "it", a=1)).json() == r.json()


async def test_an_iteration_over_1001_items_is_refused_and_creates_nothing(wf):
    await set_items(wf, list(range(1001)))
    body = (await call(wf, "it")).json()
    assert body["kind"] == "final" and body["status"] == "failed" and "exceeds maximum" in body["error"]
    assert await children(wf) == 0


async def test_fanout_finish_reads_item_rows_and_builds_the_final(wf):
    await call(wf, "it")
    async with wf.maker() as db:
        for i in range(3):
            row = await db.get(WorkflowExecution, uuid.uuid5(wf.run, f"it:{i}"))
            row.status = "completed" if i != 1 else "failed"
            row.results = {"value": i}
            row.error = "item 1 failed" if i == 1 else None
        await db.commit()
    summary = {"count": 3, "completed": 2, "failed": 1, "cancelled": 0, "firstError": "item 1 failed"}
    r = await call(wf, "it", "finish", body={**scope(wf), "contractVersion": 1, "kind": "fanout", "fanout": summary})
    body = wc.validate_prepare_response(r.json())
    assert body.status == "failed" and body.error == "item 1 failed"
    assert [i["status"] for i in body.output["items"]] == ["completed", "failed", "completed"]
    assert body.output["items"][2]["result"] == {"value": 2} and body.output["error_count"] == 1
    assert (await trace(wf))[-1]["node_id"] == "it"


# ---- finish ---------------------------------------------------------------------------------


async def test_finish_for_a_node_with_no_finish_step_is_400(wf):
    r = await call(wf, "t", "finish", body={**scope(wf), "contractVersion": 1, "kind": "llm", "answer": "x"})
    assert r.status_code == 400


@pytest.mark.parametrize("name,code", [("finish.unknown-kind.invalid.json", "invalid_body"),
                                       ("finish.fanout.missing-completed.invalid.json", "invalid_body")])
async def test_invalid_finish_fixtures_are_422_invalid_request(wf, name, code):
    fixture = json.loads((FIXTURES / name).read_text())
    r = await call(wf, "t", "finish", body={**scope(wf), **fixture})
    assert r.status_code == 422 and r.json() == {"error": "invalid_request"}


async def test_valid_finish_fixture_is_accepted_by_the_gateway(wf):
    await call(wf, "it")
    fixture = json.loads((FIXTURES / "finish.fanout.valid.json").read_text())
    r = await call(wf, "it", "finish", body={**scope(wf), **fixture})
    assert r.status_code == 200
    wc.validate_prepare_response(r.json())


async def test_prepare_rejects_unexpected_body_fields(wf):
    r = await call(wf, "t", body={**scope(wf), "contractVersion": 1, "items": [1]})
    assert r.status_code == 400


async def test_every_prepare_response_validates_against_the_contract(wf):
    for node in ("t", "cached", "d"):
        wc.validate_prepare_response((await call(wf, node)).json())


# ---- KTD4: the scope comes from the run, not the body --------------------------------------------

ENGINE_BODY = {"contractVersion": 1, "runAttempt": 0, "nodeAttempt": 0, "nodeType": "template"}


async def test_engine_shaped_prepare_body_without_tenant_or_project_runs_the_node(wf):
    r = await call(wf, "t", body=ENGINE_BODY)
    assert r.status_code == 200 and r.json()["status"] == "success"
    assert wf.counts["template"] == 1


async def test_engine_shaped_finish_body_without_scope_is_accepted(wf):
    await call(wf, "it", body=ENGINE_BODY | {"nodeType": "iteration"})
    fixture = json.loads((FIXTURES / "finish.fanout.valid.json").read_text())
    assert (await call(wf, "it", "finish", body=fixture)).status_code == 200


async def test_body_scope_that_disagrees_with_the_run_is_404(wf):
    for extra in ({"tenantId": str(wf.tenant_b)}, {"projectId": str(wf.project_b)}, {"conversationId": str(wf.conv_b)}):
        assert (await call(wf, "t", body=ENGINE_BODY | extra)).status_code == 404
    assert wf.counts["template"] == 0


async def test_unknown_run_is_404_without_a_body_scope(wf):
    other = str(uuid.uuid4())
    assert (await call(wf, "t", run=other, body=ENGINE_BODY)).status_code == 404


@pytest.mark.parametrize("patch", [{"runAttempt": 1}, {"nodeAttempt": 1}, {"runAttempt": "0"}])
async def test_body_attempts_must_agree_with_the_key(wf, patch):
    r = await call(wf, "t", body=ENGINE_BODY | patch)
    assert r.status_code == 400 and r.json()["error"] == "invalid_idempotency_key"
    assert wf.counts["template"] == 0


async def test_lookup_returns_the_stored_outcome_and_404s_otherwise(wf):
    first = await call(wf, "t", body=ENGINE_BODY)
    key = f"{wf.run}:t:r0:a0:prepare"
    hdr = {**AUTH, "X-Engine-Session": SESSION}
    found = await wf.client.get(f"/internal/workflows/invocations/{key}", headers=hdr)
    assert found.status_code == 200 and found.json() == first.json()
    gone = await wf.client.get(f"/internal/workflows/invocations/{wf.run}:t:r9:a0:prepare", headers=hdr)
    assert gone.status_code == 404 and gone.json() == {"error": "not_found"}
    other_session = await wf.client.get(
        f"/internal/workflows/invocations/{key}", headers={**AUTH, "X-Engine-Session": "someone-else"}
    )
    assert other_session.status_code == 404
    assert (await wf.client.get(f"/internal/workflows/invocations/{key}")).status_code == 401


# ---- U15: the node's input classification is computed server-side from the stored trace -------------


async def put_trace(w, *steps):
    async with w.maker() as db:
        row = await db.get(WorkflowExecution, w.run)
        row.execution_trace = [
            {"run_attempt": 0, "attempt": 0, "status": "success", "output": {}, "tokens_used": 0, **s} for s in steps
        ]
        await db.commit()


async def test_a_confidential_predecessor_blocks_an_export_controlled_node_without_any_body_hint(wf):
    await put_trace(wf, {"node_id": "t", "classification": "confidential"})
    r = await call(wf, "h", body={**scope(wf), "contractVersion": 1})  # the body names no classification
    body = wc.validate_prepare_response(r.json())
    assert body.status == "blocked" and body.classification == "confidential"
    assert wf.counts["http"] == 0 and wf.counts["audit"] == [("h", "confidential")]


async def test_a_body_that_claims_public_cannot_lower_the_stored_level(wf):
    await put_trace(wf, {"node_id": "t", "classification": "confidential"})
    body = wc.validate_prepare_response((await call(wf, "h", classification="public")).json())
    assert body.status == "blocked" and body.classification == "confidential"
    assert wf.counts["http"] == 0


async def test_confidentiality_flows_through_the_trace_from_one_node_to_the_next(wf):
    first = await call(wf, "t", classification="confidential")  # t's output is stored as confidential
    assert first.json()["classification"] == "confidential"
    blocked = wc.validate_prepare_response((await call(wf, "h", body={**scope(wf), "contractVersion": 1})).json())
    assert blocked.status == "blocked" and wf.counts["http"] == 0


async def test_an_internal_predecessor_does_not_block_the_node(wf):
    await call(wf, "t")
    await call(wf, "h", body={**scope(wf), "contractVersion": 1})
    assert wf.counts["http"] == 1  # the executor ran (the test double then fails it on purpose)
    assert wf.counts["audit"] == []


async def test_only_live_in_edge_sources_count(wf):
    await put_trace(wf, {"node_id": "bad", "classification": "confidential"})  # not an in-edge source of h
    await call(wf, "h", body={**scope(wf), "contractVersion": 1})
    assert wf.counts["http"] == 1


async def test_an_earlier_run_attempts_classification_still_counts(wf):
    await put_trace(wf, {"node_id": "t", "classification": "confidential", "run_attempt": 0})
    r = await call(wf, "h", r=1, body={**scope(wf), "contractVersion": 1})
    assert wc.validate_prepare_response(r.json()).status == "blocked"


async def test_a_stored_level_that_cannot_be_read_fails_closed(wf):
    await put_trace(wf, {"node_id": "t", "classification": "top-secret"})
    r = await call(wf, "h", body={**scope(wf), "contractVersion": 1})
    assert wc.validate_prepare_response(r.json()).status == "blocked"


async def test_fanout_items_inherit_the_iteration_nodes_input_classification(wf):
    r = await call(wf, "it", classification="confidential")
    assert r.json()["kind"] == "fanout"
    async with wf.maker() as db:
        rows = (await db.execute(select(WorkflowExecution).where(WorkflowExecution.id != wf.run))).scalars().all()
    assert len(rows) == 3
    assert {row.input_data["__classification"] for row in rows} == {"confidential"}
    assert {row.input_data["__fanout"] for row in rows} == {"iteration"}


def test_an_entry_node_takes_the_level_its_fanout_item_inherited():
    from types import SimpleNamespace

    from app.schemas.classification import DataClassification

    spec = {"nodes": [n("it", "iteration", targetNodeId="b1"), n("b1", "http"), n("b2", "http")],
            "edges": [e("it", "b1"), e("b1", "b2"), e("b2", "it", "iteration_output")]}

    def view(node_id, trace=(), variables=None):
        row = SimpleNamespace(id=uuid.uuid4(), workflow_id=None, user_id=uuid.uuid4(), tenant_id=uuid.uuid4(),
                              trigger="manual", input_data=variables or {}, execution_trace=list(trace))
        return workflow_nodes.RunView(row, spec, next(x for x in spec["nodes"] if x["id"] == node_id))

    item = {"__classification": "confidential", "__fanout": "iteration"}
    assert workflow_nodes.input_classification(view("b1", variables=item), 0).value == "confidential"
    assert workflow_nodes.input_classification(view("b1"), 0).value == "internal"  # a plain entry node
    # A body node past the entry reads its predecessor's stored level (it is not in the root graph).
    step = {"node_id": "b1", "run_attempt": 0, "classification": "confidential"}
    assert workflow_nodes.input_classification(view("b2", trace=[step]), 0).value == "confidential"
    assert workflow_nodes.input_classification(view("b1"), 0, DataClassification.CONFIDENTIAL).value == "confidential"
