"""U31 / KTD18: the memory curator is recognised by its conversation binding, mapped to the low tier, and its
usage is ledgered under the ``memory_curator`` role.

The engine only sends ``generation`` / ``compaction`` as the policy purpose, so the backend finds the curator by
the root conversation's ``engine_conversations.run_kind = "memory_curate"`` row (written by
``memory_curator.start_curation``).
"""
import uuid

from sqlalchemy import select

from app.models.engine import EngineConversation
from app.models.llm_usage import LLMUsageRecord
from tests.internal.test_model_policy import (  # noqa: F401  (fixtures and helpers)
    _env,
    add_provider,
    body,
    client,
    make_tenant,
    post,
)
from tests.internal.test_model_policy import AUTH, ANTHROPIC_KEY


async def bind(db, tenant, kind):
    conv = uuid.uuid4()
    db.add(EngineConversation(
        conversation_id=conv, tenant_id=tenant, project_id=uuid.uuid4(), user_id=uuid.uuid4(), session_id="s",
        run_kind=kind, run_ref_id=uuid.uuid4() if kind else None))
    await db.commit()
    return conv


async def test_curator_conversation_maps_to_the_low_tier(client, test_db):
    t = await make_tenant(test_db)
    await add_provider(test_db, t)
    curator, chat = await bind(test_db, t, "memory_curate"), await bind(test_db, t, None)
    low = (await post(client, body(t, purpose="compaction"))).json()["model"]
    default = (await post(client, body(t, conversationId=str(chat)))).json()["model"]
    got = (await post(client, body(t, conversationId=str(curator), engineConversationId=str(uuid.uuid4())))).json()
    assert got["ok"] is True and got["model"] == low and got["model"] != default
    assert got["apiKey"] == ANTHROPIC_KEY


async def test_curator_is_low_tier_even_when_the_agent_asks_for_high(client, test_db):
    t = await make_tenant(test_db)
    await add_provider(test_db, t)
    curator = await bind(test_db, t, "memory_curate")
    low = (await post(client, body(t, purpose="compaction"))).json()["model"]
    got = (await post(client, body(t, conversationId=str(curator), agent={"tier": "high"}))).json()
    assert got["model"] == low


async def test_a_curator_binding_of_another_tenant_is_not_trusted(client, test_db):
    a, b = await make_tenant(test_db), await make_tenant(test_db)
    await add_provider(test_db, a)
    await add_provider(test_db, b)
    foreign = await bind(test_db, b, "memory_curate")
    default = (await post(client, body(a))).json()["model"]
    got = (await post(client, body(a, conversationId=str(foreign)))).json()
    assert got["model"] == default


async def test_named_purpose_and_unknown_conversation_are_handled(client, test_db):
    t = await make_tenant(test_db)
    await add_provider(test_db, t)
    low = (await post(client, body(t, purpose="compaction"))).json()["model"]
    assert (await post(client, body(t, purpose="memory.curate"))).json()["model"] == low
    ok = await post(client, body(t, conversationId="not-a-uuid"))
    assert ok.json()["ok"] is True


def usage(tenant, conv=None, **kw):
    b = {"tenantId": str(tenant), "runId": "r1", "requestId": f"r1:0:generation-{uuid.uuid4().hex[:6]}",
         "provider": "anthropic", "model": "claude-haiku-4-5", "latencyMs": 5, "status": "success",
         "usage": {"inputTokens": 100, "outputTokens": 50, "cacheReadTokens": 0, "cacheWriteTokens": 0}}
    if conv is not None:
        b["conversationId"] = str(conv)
    b.update(kw)
    return b


async def test_curator_usage_is_ledgered_under_the_memory_curator_role(client, test_db):
    t = await make_tenant(test_db)
    curator, chat = await bind(test_db, t, "memory_curate"), await bind(test_db, t, None)
    for req in (usage(t, curator, requestId="cur"), usage(t, curator, requestId="cur-child",
                                                          engineConversationId=str(uuid.uuid4())),
                usage(t, chat, requestId="chat"), usage(t, curator, requestId="cur-comp", purpose="compaction"),
                usage(t, curator, requestId="named", agentRole="research_worker")):
        r = await client.post("/internal/models/usage", json=req, headers=AUTH)
        assert r.status_code == 200, r.text
    test_db.expire_all()
    rows = {r.request_id: (r.agent_role, r.purpose) for r in
            (await test_db.execute(select(LLMUsageRecord).where(LLMUsageRecord.tenant_id == t))).scalars().all()}
    assert rows == {
        "cur": ("memory_curator", "generation"), "cur-child": ("memory_curator", "generation"),
        "chat": (None, "generation"), "cur-comp": ("memory_curator", "compaction"),
        "named": ("research_worker", "generation"),  # an explicit role is never overwritten
    }
