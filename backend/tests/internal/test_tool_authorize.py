"""U3 - POST /internal/tools/authorize (KTD2, KTD6, KTD10)."""
import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.config import Settings
from app.core.database import get_system_db
from app.internal_api import authorize as authorize_mod
from app.internal_api.router import mount_internal_router
from app.models.engine import EngineConversation
from app.models.tenant import Tenant
from app.models.tool_approval import ToolApproval
from app.models.user import User
from app.services.tool_policy import canonical_args_hash
from tests.internal.test_manifest import by_name, loaded, manifest_json  # noqa: F401  (fixtures)
from tests.test_approval_service import FakeAudit

GATEWAY = "g" * 12 + "Gateway-Token-0123456789-abcdefXYZ"
ENGINE_SVC = "e" * 12 + "Engine-Svc-Token-0123456789-abcdXY"
SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"
AUTH = {"Authorization": f"Bearer {GATEWAY}"}
PROJECT = uuid.uuid4()


@pytest.fixture
async def env(test_db, monkeypatch):
    app = FastAPI()
    mount_internal_router(
        app, Settings(_env_file=None, SECRET_KEY=SECRET, ENGINE_GATEWAY_TOKEN=GATEWAY, ENGINE_SERVICE_TOKEN=ENGINE_SVC))

    async def _db():
        yield test_db

    app.dependency_overrides[get_system_db] = _db
    audit = FakeAudit()
    monkeypatch.setattr(authorize_mod, "make_audit", lambda db: audit)

    tenant = Tenant(id=uuid.uuid4(), name="t", slug=f"t-{uuid.uuid4().hex[:6]}", settings={})
    other = Tenant(id=uuid.uuid4(), name="o", slug=f"o-{uuid.uuid4().hex[:6]}", settings={})
    user = User(id=uuid.uuid4(), tenant_id=tenant.id, email="m@x.io", name="m", role="member", is_active=True)
    test_db.add_all([tenant, other, user])
    conv = EngineConversation(conversation_id=uuid.uuid4(), tenant_id=tenant.id, project_id=PROJECT,
                              user_id=user.id, session_id="s1")
    test_db.add(conv)
    await test_db.commit()

    class E:
        pass

    e = E()
    e.db, e.tenant, e.other, e.user, e.conv, e.audit = test_db, tenant, other, user, conv, audit
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        e.http = c
        yield e


def body(env, tool="execute_python", n=1, **kw):
    args = kw.pop("arguments", {"code": "print(1)"})
    b = {
        "requestId": f"r1:c{n}", "tenantId": str(env.tenant.id), "projectId": str(PROJECT),
        "conversationId": str(env.conv.conversation_id), "engineConversationId": str(env.conv.conversation_id),
        "runId": "r1", "toolCallId": f"c{n}", "toolTaskId": f"t{n}", "toolName": tool, "toolSource": "gateway",
        "arguments": args, "argumentsHash": canonical_args_hash(args), "sideEffect": "write",
        "replay": {"mode": "never"}, "taint": {"tainted": False, "sources": []},
    }
    b.update(kw)
    return b


async def post(env, b, headers=AUTH):
    return await env.http.post("/internal/tools/authorize", json=b, headers=headers)


async def rows(env):
    return list((await env.db.execute(select(ToolApproval))).scalars().all())


async def test_missing_or_bad_bearer_is_401(env):
    assert (await post(env, body(env), headers={})).status_code == 401
    assert (await post(env, body(env), headers={"Authorization": "Bearer nope"})).status_code == 401


async def test_conversation_of_another_tenant_is_404(env):
    r = await post(env, body(env, tenantId=str(env.other.id)))
    assert r.status_code == 404
    assert await rows(env) == []


async def test_requires_approval_creates_one_scrubbed_row_even_when_posted_twice(env):
    args = {"code": "print(1)", "api_key": "sk-live-abcdefghijklmnop"}
    b = body(env, arguments=args)
    first = (await post(env, b)).json()
    second = (await post(env, b)).json()
    assert first["decision"] == "requireApproval" and first["requiredRole"] == "admin"
    assert first["approvalTtlMs"] == 168 * 3_600_000 and first["reason"]
    assert second == first
    [row] = await rows(env)
    assert str(row.id) == first["approvalId"]
    assert row.arguments_hash == canonical_args_hash(args)
    assert "api_key" not in row.arguments_preview
    assert (row.requested_by, row.approver_role, row.tool_task_id) == (env.user.id, "admin", "t1")
    assert [e.action for e in env.audit.entries] == ["approval_requested"]  # not written twice


async def test_ttl_zero_means_no_expiry(env):
    env.tenant.settings = {"approvals": {"ttl_hours": 0}}
    await env.db.commit()
    r = (await post(env, body(env))).json()
    assert r["approvalTtlMs"] is None
    assert (await rows(env))[0].expires_at is None


async def test_tainted_external_write_requires_rule_of_two_approval_for_worker(env):
    t = {"tainted": True, "sources": [{"kind": "tool_result", "name": "browse_url", "ref": "x", "at": 1}]}
    r = (await post(env, body(env, tool="linear_search_issues", taint=t, agentRole="research_worker"))).json()
    assert r["decision"] == "allow"  # a read
    # an external write in the manifest that does not itself require approval
    from app.internal_api import tool_specs
    spec = tool_specs.ToolSpec(side_effect="write", scope="external")
    tool_specs.TOOL_SPECS["tmp_external_write"] = spec
    from app.agents.skills import registry as reg
    reg._registry["tmp_external_write"] = object()
    try:
        r = (await post(env, body(env, n=2, tool="tmp_external_write", taint=t, agentRole="research_worker"))).json()
    finally:
        tool_specs.TOOL_SPECS.pop("tmp_external_write", None)
        reg._registry.pop("tmp_external_write", None)
    assert r["decision"] == "requireApproval" and r["requiredRole"] == "member"
    [row] = [x for x in await rows(env) if x.tool_call_id == "c2"]
    assert row.rule == "rule_of_two" and row.requested_by == env.user.id
    assert row.taint_sources[0]["name"] == "browse_url"


async def test_unknown_gateway_tool_blocks_with_audit(env):
    r = (await post(env, body(env, tool="nope_tool"))).json()
    assert (r["decision"], r["code"]) == ("block", "unknown_tool")
    assert await rows(env) == []
    assert [e.action for e in env.audit.entries] == ["tool_policy_block"]


async def test_sandbox_and_workspace_allow_when_tainted(env):
    t = {"tainted": True, "sources": []}
    for n, tool in enumerate(("shell_exec", "write_file"), start=1):
        assert (await post(env, body(env, n=n, tool=tool, taint=t))).json() == {"decision": "allow"}
    assert await rows(env) == []


async def test_native_allowlisted_allows_and_other_native_requires_approval(env):
    assert (await post(env, body(env, tool="update_todo", toolSource="native"))).json() == {"decision": "allow"}
    r = (await post(env, body(env, n=2, tool="mystery", toolSource="native"))).json()
    assert r["decision"] == "requireApproval" and r["reason"] == "Tool is not on the internal allowlist"


async def test_ask_user_allows_and_indexes_a_question_row(env):
    b = body(env, tool="ask_user", toolSource="native", arguments={"question": "which?"})
    assert (await post(env, b)).json() == {"decision": "allow"}
    assert (await post(env, b)).json() == {"decision": "allow"}
    [row] = await rows(env)
    assert (row.kind, row.status, row.tool_task_id) == ("question", "pending", "t1")


def test_attended_is_derived_from_the_conversation_owner():
    from types import SimpleNamespace
    assert authorize_mod.is_attended(SimpleNamespace(user_id=uuid.uuid4())) is True
    assert authorize_mod.is_attended(SimpleNamespace(user_id=None)) is False


def test_manifest_flags_untrusted_output(manifest_json):  # noqa: F811
    tools = by_name(manifest_json)
    assert tools["browse_url"]["untrustedOutput"] is True
    assert tools["write_file"]["untrustedOutput"] is False
