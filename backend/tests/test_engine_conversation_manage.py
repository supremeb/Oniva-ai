"""Sidebar rename / pin / archive for engine conversations (PATCH and DELETE /{id})."""
import uuid

from tests.test_engine_chat_flag import Env, au, tenant

BASE = "/api/v1/engine/conversations"


def setup():
    t = tenant(True)
    e = Env([t], au(t))
    return t, e, e.seed(t)


def test_patch_renames_and_pins_without_touching_engine():
    t, e, cid = setup()
    r = e.http.patch(f"{BASE}/{cid}", json={"title": "  Q3   plan ", "pinned": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"conversationId": cid, "title": "Q3 plan", "pinned": True}
    row = e.db.rows[uuid.UUID(cid)]
    assert row.title == "Q3 plan" and row.pinned is True
    assert e.calls == []
    # partial update keeps the other field
    assert e.http.patch(f"{BASE}/{cid}", json={"pinned": False}).json()["title"] == "Q3 plan"


def test_patch_rejects_empty_or_long_title():
    t, e, cid = setup()
    assert e.http.patch(f"{BASE}/{cid}", json={"title": "   "}).status_code == 422
    assert e.http.patch(f"{BASE}/{cid}", json={"title": "x" * 256}).status_code == 422


def test_delete_archives_and_is_idempotent():
    t, e, cid = setup()
    assert e.http.delete(f"{BASE}/{cid}").status_code == 204
    row = e.db.rows[uuid.UUID(cid)]  # row kept: data is not deleted
    first = row.archived_at
    assert first is not None
    assert e.http.delete(f"{BASE}/{cid}").status_code == 204
    assert row.archived_at == first
    assert e.calls == []
    assert e.http.patch(f"{BASE}/{cid}", json={"title": "x"}).status_code == 404


def test_owner_or_admin_only_and_tenant_scoped():
    t, e, cid = setup()
    row = e.db.rows[uuid.UUID(cid)]
    row.user_id = uuid.uuid4()  # someone else's conversation
    assert e.http.patch(f"{BASE}/{cid}", json={"pinned": True}).status_code == 403
    assert e.http.delete(f"{BASE}/{cid}").status_code == 403
    e.current.role = "admin"
    assert e.http.patch(f"{BASE}/{cid}", json={"pinned": True}).status_code == 200
    row.tenant_id = uuid.uuid4()
    assert e.http.delete(f"{BASE}/{cid}").status_code == 404


def test_flag_gate_applies():
    t = tenant(False)
    e = Env([t], au(t))
    cid = e.seed(t)
    assert e.http.patch(f"{BASE}/{cid}", json={"pinned": True}).status_code == 404
    assert e.http.delete(f"{BASE}/{cid}").status_code == 404
