"""U10 / R17: the Python endpoints reproduce every clause of the engine's fake gateway.

`engine/test/support/fake-gateway.ts` is the normative contract. Its TSDoc tags each behavior
`[clause:<id>]`; every tag must have exactly one test here, marked `@clause("<id>")`, and
`test_every_fake_gateway_clause_has_a_conformance_test` fails when a clause is added to the
TypeScript fake without a matching test (or a test names a clause the fake no longer has).

The scenarios reuse the fixtures of test_invoke.py (tools/fence/reconcile, per-request SQLite
connections) and test_model_policy.py (policy and usage on the system session).
"""
import asyncio
import re
import uuid
from contextlib import suppress
from pathlib import Path

import pytest

from app.agents.skills.registry import Skill, _registry
from app.internal_api import invocations, tools
from app.internal_api.execution import MAX_OUTCOME_BYTES, ToolRateLimited
from app.models.engine import EngineConversation
from tests.internal import test_invoke as ti
from tests.internal import test_model_policy as tp
from tests.internal.test_invoke import effects, factory, gw, maker  # noqa: F401  (fixtures)
from tests.internal.test_model_policy import _env, client  # noqa: F401  (fixtures)

FAKE_GATEWAY = Path(__file__).resolve().parents[3] / "engine" / "test" / "support" / "fake-gateway.ts"
CLAUSE_TAG = re.compile(r"\[clause:([a-z0-9-]+)\]")
ENGINE_RESPONSE_CAP = 1024 * 1024

COVERED: set[str] = set()


def clause(clause_id: str):
    """Mark a test as the conformance test of one fake-gateway clause."""

    def mark(fn):
        assert clause_id not in COVERED, f"clause {clause_id} has two tests"
        COVERED.add(clause_id)
        return fn

    return mark


def fake_gateway_clauses() -> list[str]:
    doc = FAKE_GATEWAY.read_text()
    doc = doc[: doc.index("*/")]  # the file's leading TSDoc is the contract
    return CLAUSE_TAG.findall(doc)


# ---- drift guard ----------------------------------------------------------------------------


def test_every_fake_gateway_clause_has_a_conformance_test():
    declared = fake_gateway_clauses()
    assert declared, "no [clause:...] tags found in fake-gateway.ts"
    assert len(declared) == len(set(declared)), "a clause id is declared twice in fake-gateway.ts"
    missing = sorted(set(declared) - COVERED)
    stale = sorted(COVERED - set(declared))
    assert not missing, f"fake-gateway.ts clauses without a conformance test: {missing}"
    assert not stale, f"conformance tests for clauses the fake no longer declares: {stale}"


# ---- auth ----------------------------------------------------------------------------------


@clause("auth-401")
async def test_auth_401(gw, client):
    bad = {"Authorization": "Bearer nope"}
    for h in ({}, bad):
        assert (await gw.client.get("/internal/tools/manifest", headers=h)).status_code == 401
        r = await gw.client.post("/internal/tools/spy_write/invoke", json=ti.body(gw), headers={**h, "Idempotency-Key": "r1:c1"})
        assert r.status_code == 401
        assert (await gw.client.get("/internal/tools/invocations/r1:c1", headers=h)).status_code == 401
        assert (await client.post("/internal/models/policy", json={}, headers=h)).status_code == 401
        assert (await client.post("/internal/models/usage", json={}, headers=h)).status_code == 401
    assert gw.effects.counts["spy_write"] == 0


# ---- fencing --------------------------------------------------------------------------------


@clause("fence-409")
async def test_fence_409(gw):
    assert (await ti.invoke(gw, token=7, call="c1")).status_code == 200
    r = await ti.invoke(gw, token=6, call="c2")
    assert r.status_code == 409 and r.json() == {"error": "fence_rejected", "highWater": 7}
    assert gw.effects.counts["spy_write"] == 1
    assert [x.idempotency_key for x in await ti.rows(gw)] == ["r1:c1"]


@clause("fence-raise-on-read")
async def test_fence_raise_on_read(gw):
    assert (await gw.client.get("/internal/tools/manifest", headers=ti.fence_only(7))).status_code == 200
    assert (await gw.client.get("/internal/tools/invocations/x:y", headers=ti.fence_only(8))).status_code == 404
    assert await ti.high_water(gw) == 8
    assert (await ti.invoke(gw, token=7)).status_code == 409
    assert gw.effects.counts["spy_write"] == 0


@clause("fence-invalid-400")
async def test_fence_invalid_400(gw):
    for bad in ("abc", "-1", "1.5"):
        r = await gw.client.post(
            "/internal/tools/spy_write/invoke", json=ti.body(gw), headers={**ti.headers(), "X-Engine-Fence": bad}
        )
        assert r.status_code == 400 and r.json() == {"error": "invalid_fence"}, bad
    assert gw.effects.counts["spy_write"] == 0


@clause("fence-invoke-session-default")
async def test_fence_invoke_session_default(gw):
    key = f"{gw.tenant_a}/{gw.project_a}"
    assert (await ti.invoke(gw, token=4, session=None)).status_code == 200
    assert await ti.high_water(gw, key) == 4
    assert (await ti.invoke(gw, token=3, session=None, call="c2")).status_code == 409


@clause("fence-sessionless-unchecked")
async def test_fence_sessionless_unchecked(gw):
    await gw.client.get("/internal/tools/manifest", headers=ti.fence_only(7))
    no_session = {**ti.AUTH, "X-Engine-Fence": "1"}
    assert (await gw.client.get("/internal/tools/manifest", headers=no_session)).status_code == 200
    assert (await gw.client.get("/internal/tools/invocations/r1:c1", headers=no_session)).status_code == 404


@clause("fence-commit-recheck")
async def test_fence_commit_recheck(gw, monkeypatch):
    real_begin = invocations.begin

    async def begin_then_successor_raises(*a, **kw):
        started = await real_begin(*a, **kw)
        await gw.client.get("/internal/tools/manifest", headers=ti.fence_only(7))
        return started

    monkeypatch.setattr(invocations, "begin", begin_then_successor_raises)
    r = await ti.invoke(gw, token=6)
    assert r.status_code == 409 and r.json()["highWater"] == 7
    assert gw.effects.counts["spy_write"] == 0 and await ti.rows(gw) == []
    rec = await gw.client.get("/internal/tools/invocations/r1:c1", headers=ti.fence_only(7))
    assert rec.status_code == 404  # final


# ---- manifest -------------------------------------------------------------------------------


@clause("manifest-shape")
async def test_manifest_shape(gw):
    r = await gw.client.get("/internal/tools/manifest", headers=ti.AUTH)
    assert r.status_code == 200
    listed = r.json()["tools"]
    assert listed
    for t in listed:
        assert isinstance(t["name"], str) and isinstance(t["description"], str)
        assert t["parameters"]["type"] == "object"
        assert t["sideEffect"] in ("none", "write")
        assert isinstance(t["idempotent"], bool) and isinstance(t["requiresApproval"], bool)
        assert isinstance(t["timeoutMs"], int) and t["timeoutMs"] > 0
    by_name = {t["name"]: t for t in listed}
    assert by_name["spy_read"]["sideEffect"] == "none"
    assert by_name["spy_idem"]["sideEffect"] == "write" and by_name["spy_idem"]["idempotent"] is True
    assert by_name["spy_write"]["idempotent"] is False


# ---- invoke ---------------------------------------------------------------------------------


@clause("invoke-outcome-shape")
async def test_invoke_outcome_shape(gw):
    r = await ti.invoke(gw, "spy_read")
    assert r.status_code == 200
    out = r.json()
    assert isinstance(out["content"], str) or all(c["type"] == "text" for c in out["content"])
    assert isinstance(out["isError"], bool)


@clause("invoke-replay-stored")
async def test_invoke_replay_stored(gw):
    first = await ti.invoke(gw, token=7)
    again = await ti.invoke(gw, token=7)
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    assert gw.effects.counts["spy_write"] == 1


@clause("invoke-unknown-tool-404")
async def test_invoke_unknown_tool_404(gw):
    r = await gw.client.post("/internal/tools/nope/invoke", json=ti.body(gw), headers=ti.headers())
    assert r.status_code == 404 and r.json() == {"error": "unknown_tool"}


@clause("invoke-invalid-400")
async def test_invoke_invalid_400(gw):
    bad_bodies = [{"runId": "r1", "toolCallId": "c1"}, {**ti.body(gw), "args": []}, {**ti.body(gw), "tenantId": 5}]
    for b in bad_bodies:
        r = await gw.client.post("/internal/tools/spy_write/invoke", json=b, headers=ti.headers())
        assert r.status_code == 400 and r.json()["error"] == "invalid_request", b
    for key in ("r1:other", "r1"):
        r = await gw.client.post(
            "/internal/tools/spy_write/invoke", json=ti.body(gw), headers={**ti.headers(), "Idempotency-Key": key}
        )
        assert r.status_code == 400 and r.json()["error"] == "invalid_request", key
    assert gw.effects.counts["spy_write"] == 0 and await ti.rows(gw) == []


@clause("invoke-retry-after")
async def test_invoke_retry_after(gw, monkeypatch):
    await ti.stuck_claim(gw, "spy_idem")
    busy = await ti.invoke(gw, "spy_idem")
    assert busy.status_code == 503 and busy.headers["retry-after"] == "1"

    async def limited(*a, **kw):
        raise ToolRateLimited(7)

    monkeypatch.setattr(tools, "execute_tool", limited)
    r = await ti.invoke(gw, "spy_read", call="c9")
    assert r.status_code == 429 and r.headers["retry-after"] == "7"
    assert gw.effects.counts["spy_idem"] == 0 and gw.effects.counts["spy_read"] == 0


@clause("invoke-survives-disconnect")
async def test_invoke_survives_disconnect(gw):
    gw.effects.gate["hold"] = True
    request = asyncio.create_task(ti.invoke(gw, token=6))
    await asyncio.wait_for(gw.effects.gate["start"].wait(), 5)
    request.cancel()
    with suppress(asyncio.CancelledError):
        await request
    gw.effects.gate["release"].set()
    await asyncio.wait_for(asyncio.gather(*list(tools._effects)), 5)
    assert gw.effects.counts["spy_write"] == 1
    rec = await gw.client.get("/internal/tools/invocations/r1:c1", headers=ti.fence_only(6))
    assert rec.status_code == 200 and rec.json()["isError"] is False


@clause("outcome-size-cap")
async def test_outcome_size_cap(gw):
    async def huge(ctx, path: str = "a"):
        return "a" * (MAX_OUTCOME_BYTES * 4)

    original = _registry["spy_write"]
    _registry["spy_write"] = Skill(name="spy_write", description="w", category="test", handler=huge)
    try:
        r = await ti.invoke(gw, token=6)
    finally:
        _registry["spy_write"] = original
    assert r.status_code == 200
    assert len(r.content) < ENGINE_RESPONSE_CAP
    assert r.json()["details"]["truncated"] is True
    stored = await gw.client.get("/internal/tools/invocations/r1:c1", headers=ti.fence_only(6))
    assert len(stored.content) < ENGINE_RESPONSE_CAP


# ---- reconcile ------------------------------------------------------------------------------


@clause("reconcile-found")
async def test_reconcile_found(gw):
    r = await ti.invoke(gw, token=6)
    rec = await gw.client.get("/internal/tools/invocations/r1:c1", headers=ti.fence_only(6))
    assert rec.status_code == 200 and rec.json() == r.json()
    assert gw.effects.counts["spy_write"] == 1


@clause("reconcile-not-found")
async def test_reconcile_not_found(gw):
    unknown = await gw.client.get("/internal/tools/invocations/nope:nope", headers=ti.fence_only(1))
    assert unknown.status_code == 404 and unknown.json() == {"error": "not_found"}
    await ti.stuck_claim(gw, "spy_idem")  # a call still in flight
    inflight = await gw.client.get("/internal/tools/invocations/r1:c1", headers=ti.fence_only(1))
    assert inflight.status_code == 404 and inflight.json() == {"error": "not_found"}


@clause("reconcile-fenced")
async def test_reconcile_fenced(gw):
    await gw.client.get("/internal/tools/manifest", headers=ti.fence_only(7))
    r = await gw.client.get("/internal/tools/invocations/r1:c1", headers=ti.fence_only(5))
    assert r.status_code == 409 and r.json() == {"error": "fence_rejected", "highWater": 7}


# ---- conversation binding -------------------------------------------------------------------


@clause("invoke-conversation-id")
async def test_invoke_conversation_id(gw):
    with_conv = await ti.invoke(gw)
    assert with_conv.status_code == 200 and str(gw.user_a) in with_conv.json()["content"]
    # An older client without it still works (for a tenant that has no bound conversations).
    async with gw.maker() as db:
        await db.execute(EngineConversation.__table__.delete().where(EngineConversation.tenant_id == gw.tenant_a))
        await db.commit()
    assert (await ti.invoke(gw, conversation=False, call="c2")).status_code == 200


@clause("engine-conversation-id")
async def test_engine_conversation_id(gw, client, test_db):
    b = {**ti.body(gw), "engineConversationId": "conv_child_01"}
    r = await gw.client.post("/internal/tools/spy_write/invoke", json=b, headers=ti.headers())
    assert r.status_code == 200 and str(gw.user_a) in r.json()["content"]  # attributed via the root

    t = await tp.make_tenant(test_db)
    await tp.add_provider(test_db, t)
    p = await tp.post(client, tp.body(t, engineConversationId="conv_child_01"))
    assert p.status_code == 200 and p.json()["ok"] is True
    u = await client.post(
        "/internal/models/usage", headers=tp.AUTH,
        json=_usage(t, engineConversationId="conv_child_01"),
    )
    assert u.status_code == 200 and u.json() == {"recorded": True}


# ---- model policy and usage -----------------------------------------------------------------


def _usage(tenant, **kw):
    b = {
        "tenantId": str(tenant), "runId": "r1", "requestId": f"r1:0:generation-{uuid.uuid4().hex[:6]}",
        "provider": "anthropic", "model": "claude-sonnet-4-5", "latencyMs": 5, "status": "success",
        "usage": {"inputTokens": 10, "outputTokens": 5, "cacheReadTokens": 0, "cacheWriteTokens": 0},
    }
    b.update(kw)
    return b


@clause("models-unfenced")
async def test_models_unfenced(client, test_db):
    t = await tp.make_tenant(test_db)
    await tp.add_provider(test_db, t)
    fence = {**tp.AUTH, "X-Engine-Fence": "1", "X-Engine-Session": "s"}
    other = {**tp.AUTH, "X-Engine-Fence": "99", "X-Engine-Session": "s"}
    for h in (other, fence):  # a lower token after a higher one is not rejected
        assert (await client.post("/internal/models/policy", json=tp.body(t), headers=h)).status_code == 200
        assert (await client.post("/internal/models/usage", json=_usage(t), headers=h)).status_code == 200


@clause("policy-no-store")
async def test_policy_no_store(client, test_db):
    t = await tp.make_tenant(test_db)
    await tp.add_provider(test_db, t)
    ok = await tp.post(client, tp.body(t))
    denied = await tp.post(client, tp.body(await tp.make_tenant(test_db)))  # no key -> denial
    assert ok.json()["ok"] is True and denied.json()["ok"] is False
    assert ok.headers["cache-control"] == denied.headers["cache-control"] == "no-store"


@clause("policy-allow-shape")
async def test_policy_allow_shape(client, test_db):
    t = await tp.make_tenant(test_db)
    await tp.add_provider(test_db, t, default_model="anthropic/claude-sonnet-4-5")
    payload = tp.body(t)
    r = await tp.post(client, payload)
    j = r.json()
    assert r.status_code == 200 and j["ok"] is True
    assert set(j) >= {"ok", "provider", "model", "apiKey", "maxTokens", "expiresAt", "requestId"}
    assert j["provider"] == "anthropic" and j["model"] == "claude-sonnet-5-5"
    assert j["apiKey"] == tp.ANTHROPIC_KEY and isinstance(j["maxTokens"], int) and j["maxTokens"] > 0
    assert isinstance(j["expiresAt"], int) and j["expiresAt"] > 1_000_000_000_000  # epoch ms
    assert j["requestId"] == payload["requestId"]


@clause("policy-denial-shape")
async def test_policy_denial_shape(client, test_db):
    r = await tp.post(client, tp.body(await tp.make_tenant(test_db)))
    j = r.json()
    assert r.status_code == 200 and j["ok"] is False
    assert isinstance(j["code"], str) and isinstance(j["reason"], str) and isinstance(j["retryable"], bool)
    assert "apiKey" not in j


@clause("policy-invalid-422")
async def test_policy_invalid_422(client, test_db):
    t = await tp.make_tenant(test_db)
    for missing in ("requestId", "tenantId"):
        payload = tp.body(t)
        del payload[missing]
        assert (await tp.post(client, payload)).status_code == 422, missing


@clause("usage-recorded")
async def test_usage_recorded(client, test_db):
    t = await tp.make_tenant(test_db)
    r = await client.post("/internal/models/usage", json=_usage(t), headers=tp.AUTH)
    assert r.status_code == 200 and r.json() == {"recorded": True}
    assert r.headers["cache-control"] == "no-store"


@clause("usage-repeat-false")
async def test_usage_repeat_false(client, test_db):
    t = await tp.make_tenant(test_db)
    payload = _usage(t)
    first = await client.post("/internal/models/usage", json=payload, headers=tp.AUTH)
    again = await client.post("/internal/models/usage", json=payload, headers=tp.AUTH)
    assert first.json() == {"recorded": True} and again.json() == {"recorded": False}
    other = await tp.make_tenant(test_db)  # the key is per (tenant, requestId)
    third = await client.post("/internal/models/usage", json={**payload, "tenantId": str(other)}, headers=tp.AUTH)
    assert third.json() == {"recorded": True}


@clause("usage-invalid-422")
async def test_usage_invalid_422(client, test_db):
    t = await tp.make_tenant(test_db)
    for missing in ("requestId", "tenantId", "runId", "provider", "model"):
        payload = _usage(t)
        del payload[missing]
        r = await client.post("/internal/models/usage", json=payload, headers=tp.AUTH)
        assert r.status_code == 422, missing


async def _rows(db, tenant):
    from sqlalchemy import select

    from app.models.llm_usage import LLMUsageRecord

    db.expire_all()
    return (await db.execute(select(LLMUsageRecord).where(LLMUsageRecord.tenant_id == tenant))).scalars().all()


@clause("usage-agent-role")
async def test_usage_agent_role_optional_and_stored(client, test_db):
    t = await tp.make_tenant(test_db)
    child = _usage(t, agentRole="research_worker")
    root = _usage(t)
    for p in (child, root):
        r = await client.post("/internal/models/usage", json=p, headers=tp.AUTH)
        assert r.status_code == 200 and r.json() == {"recorded": True}
    by_req = {r.request_id: r.agent_role for r in await _rows(test_db, t)}
    assert by_req == {child["requestId"]: "research_worker", root["requestId"]: None}


@clause("usage-agent-role-422")
async def test_usage_agent_role_malformed_422(client, test_db):
    t = await tp.make_tenant(test_db)
    for bad in ("Bad Role", "x" * 65, "has-dash", ""):
        r = await client.post("/internal/models/usage", json=_usage(t, agentRole=bad), headers=tp.AUTH)
        assert r.status_code == 422, bad
