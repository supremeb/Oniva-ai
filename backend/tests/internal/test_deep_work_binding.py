"""Deep Work U3 / R9, R17: worker-originated gateway calls bind through the ROOT conversation.

The engine sends the ROOT conversation id as ``conversationId`` and the (child) worker conversation
id as the informational ``engineConversationId`` on every gateway body, so the tool runs as the root
conversation's user and a child id alone never resolves.
"""
import uuid

from tests.internal.test_invoke import (  # noqa: F401  (fixtures)
    body, effects, factory, gw, headers, invoke, maker, rows,
)

CHILD = "conv_child_01"


async def post_worker(w, *, conversation_id, tenant="a", call="c1", child=CHILD):
    b = body(w, tenant=tenant, call=call, conversation=False)
    if conversation_id is not None:
        b["conversationId"] = str(conversation_id)
    b["engineConversationId"] = child
    return await w.client.post("/internal/tools/spy_write/invoke", json=b, headers=headers(call=call))


async def test_worker_call_with_a_valid_root_runs_as_the_root_user(gw):
    r = await post_worker(gw, conversation_id=gw.conv_a)
    assert r.status_code == 200 and r.json()["isError"] is False
    assert str(gw.user_a) in r.json()["content"]
    assert gw.effects.counts["spy_write"] == 1


async def test_root_from_another_tenant_is_404_and_runs_nothing(gw):
    r = await post_worker(gw, conversation_id=gw.conv_b)  # tenant a / project a naming tenant b's root
    assert r.status_code == 404 and r.json() == {"error": "conversation_not_found"}
    assert gw.effects.counts["spy_write"] == 0
    assert await rows(gw) == []


async def test_a_child_id_alone_does_not_resolve(gw):
    r = await post_worker(gw, conversation_id=uuid.uuid4())  # a child conversation has no binding row
    assert r.status_code == 404 and r.json() == {"error": "conversation_not_found"}
    assert gw.effects.counts["spy_write"] == 0


async def test_no_conversation_is_400_once_the_tenant_has_conversations(gw):
    r = await post_worker(gw, conversation_id=None)
    assert r.status_code == 400 and r.json() == {"error": "conversation_required"}
    assert gw.effects.counts["spy_write"] == 0


async def test_root_in_another_project_is_404(gw):
    b = body(gw)
    b["projectId"] = str(uuid.uuid4())
    b["engineConversationId"] = CHILD
    r = await gw.client.post("/internal/tools/spy_write/invoke", json=b, headers=headers())
    assert r.status_code == 404
