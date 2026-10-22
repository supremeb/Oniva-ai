"""U32 - /api/v1/projects/{id}/memory routes on SQLite: flag, RBAC matrix, 404 for outsiders, edit conflicts,
pending approve/reject, export, purge, audit events, and the retention sweep."""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.api.v1 import project_memory as api
from app.auth.dependencies import get_current_user
from app.auth.providers.base import AuthUser
from app.core.database import get_db
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.project_memory import MemoryBullet, MemoryFile
from app.models.tenant import Tenant
from app.models.user import User
from app.services import project_memory as pm
from app.services.memory_retention import sweep_expired

TENANT = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()
PROJECT = uuid.uuid4()
OTHER_PROJECT = uuid.uuid4()
BASE = f"/api/v1/projects/{PROJECT}/memory"


class FakeAudit:
    """Records ``AuditChain.append`` calls (the chain's ARRAY columns need Postgres; that tier is tested there)."""

    def __init__(self):
        self.entries = []

    async def append(self, **kw):
        self.entries.append(SimpleNamespace(**kw))
        return self.entries[-1]


def auth(user: User) -> AuthUser:
    return AuthUser(id=str(user.id), email=user.email, name=user.name, tenant_id=str(user.tenant_id),
                    tenant_slug="t", role=user.role, auth_provider="local")


@pytest.fixture
async def world(test_db):
    db = test_db
    db.add(Tenant(id=TENANT, name="t", slug="t", settings={"features": {"ENABLE_PROJECT_MEMORY": True}}))
    db.add(Tenant(id=OTHER_TENANT, name="o", slug="o", settings={"features": {"ENABLE_PROJECT_MEMORY": True}}))
    users = {}
    for name, role, tenant in (
        ("powner", "member", TENANT),     # owns the project
        ("editor", "member", TENANT),     # project editor
        ("pviewer", "member", TENANT),    # project viewer, tenant member
        ("tviewer", "viewer", TENANT),    # tenant viewer, project editor
        ("outsider", "member", TENANT),   # no project access
        ("admin", "admin", TENANT),       # tenant admin, not a project member
        ("stranger", "admin", OTHER_TENANT),
    ):
        u = User(id=uuid.uuid4(), tenant_id=tenant, email=f"{name}@x.io", name=name, role=role, is_active=True)
        db.add(u)
        users[name] = u
    await db.commit()
    db.add(Project(id=PROJECT, tenant_id=TENANT, owner_id=users["powner"].id, name="p"))
    db.add(Project(id=OTHER_PROJECT, tenant_id=OTHER_TENANT, owner_id=users["stranger"].id, name="o"))
    await db.commit()
    for name, role in (("editor", "editor"), ("pviewer", "viewer"), ("tviewer", "editor")):
        db.add(ProjectMember(project_id=PROJECT, user_id=users[name].id, role=role, tenant_id=TENANT))
    await db.commit()

    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1/projects")
    audit = FakeAudit()

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[api.get_memory_audit] = lambda: audit

    w = SimpleNamespace(db=db, users=users, app=app, audit=audit)
    w.as_user = lambda name: app.dependency_overrides.__setitem__(get_current_user, lambda: auth(users[name]))
    w.seed_file = lambda path="core.md", content="hello", status="active", **kw: pm.create(
        db, tenant_id=TENANT, project_id=PROJECT, path=path, content=content, source_kind="curator",
        status=status, **kw)
    return w


@pytest.fixture
async def client(world):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=world.app), base_url="http://t") as c:
        yield c


async def seed_bullet(db, key="b1", status="active", text="Prefer short answers", tenant=TENANT, project=PROJECT):
    row = MemoryBullet(tenant_id=tenant, project_id=project, bullet_key=key, section="style", text=text,
                       status=status, source_kind="curator")
    db.add(row)
    await db.commit()
    return row


# --- auth, flag ----------------------------------------------------------------------------------

async def test_requires_authentication(client):
    assert (await client.get(BASE)).status_code in (401, 403)


async def test_flag_off_is_404_for_every_route(client, world):
    t = (await world.db.execute(select(Tenant).where(Tenant.id == TENANT))).scalars().one()
    t.settings = {"features": {"ENABLE_PROJECT_MEMORY": False}}
    await world.db.commit()
    world.as_user("admin")
    row = await seed_bullet(world.db)
    calls = [
        client.get(BASE), client.put(f"{BASE}/files/core.md", json={"content": "x"}),
        client.delete(f"{BASE}/files/core.md"), client.patch(f"{BASE}/bullets/{row.id}", json={"text": "y"}),
        client.delete(f"{BASE}/bullets/{row.id}"), client.post(f"{BASE}/pending/{row.id}/approve"),
        client.post(f"{BASE}/pending/{row.id}/reject"), client.post(f"{BASE}/export"), client.delete(BASE),
    ]
    for c in calls:
        assert (await c).status_code == 404


async def test_global_flag_default_off_without_tenant_override(client, world):
    t = (await world.db.execute(select(Tenant).where(Tenant.id == TENANT))).scalars().one()
    t.settings = {}
    await world.db.commit()
    world.as_user("admin")
    assert (await client.get(BASE)).status_code == 404


# --- RBAC matrix ---------------------------------------------------------------------------------

@pytest.mark.parametrize("who,expected", [
    ("powner", 200), ("editor", 200), ("pviewer", 200), ("tviewer", 200), ("admin", 404),  # admin is no member
    ("outsider", 404), ("stranger", 404),
])
async def test_read_matrix(client, world, who, expected):
    await world.seed_file()
    await world.db.commit()
    world.as_user(who)
    r = await client.get(BASE)
    assert r.status_code == expected
    if expected == 200:
        assert [f["path"] for f in r.json()["files"]] == ["core.md"]


@pytest.mark.parametrize("who,expected", [
    ("powner", 200),      # project owner, tenant member
    ("editor", 200),      # project editor
    ("pviewer", 403),     # project viewer
    ("tviewer", 403),     # tenant viewer lacks project_memory:write
    ("outsider", 404),    # not in the project: existence is not revealed
    ("stranger", 404),    # another tenant
])
async def test_write_matrix(client, world, who, expected):
    world.as_user(who)
    r = await client.put(f"{BASE}/files/core.md", json={"content": "x"})
    assert r.status_code == expected, r.text


@pytest.mark.parametrize("who,expected", [("admin", 200), ("powner", 403), ("editor", 403), ("tviewer", 403),
                                           ("stranger", 404)])
async def test_purge_matrix(client, world, who, expected):
    world.as_user(who)
    assert (await client.delete(BASE)).status_code == expected


async def test_viewer_cannot_use_any_write_route(client, world):
    row = await seed_bullet(world.db, status="pending")
    world.as_user("pviewer")
    for resp in (
        await client.put(f"{BASE}/files/a.md", json={"content": "x"}),
        await client.delete(f"{BASE}/files/a.md"),
        await client.patch(f"{BASE}/bullets/{row.id}", json={"text": "y"}),
        await client.delete(f"{BASE}/bullets/{row.id}"),
        await client.post(f"{BASE}/pending/{row.id}/approve"),
        await client.post(f"{BASE}/pending/{row.id}/reject"),
    ):
        assert resp.status_code == 403
    assert (await client.post(f"{BASE}/export")).status_code == 200  # export is a read


async def test_other_tenants_project_is_404_even_for_an_admin(client, world):
    world.as_user("stranger")
    assert (await client.get(f"/api/v1/projects/{PROJECT}/memory")).status_code == 404
    assert (await client.get(f"/api/v1/projects/{OTHER_PROJECT}/memory")).status_code == 200


# --- files: edit, conflicts, errors ----------------------------------------------------------------

async def test_put_creates_then_updates_with_version_check(client, world):
    world.as_user("editor")
    r = await client.put(f"{BASE}/files/notes/team.md", json={"content": "v1"})
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["source_kind"] == "user"
    r = await client.put(f"{BASE}/files/notes/team.md", json={"content": "v2", "expected_version": 1})
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["content"] == "v2"
    stale = await client.put(f"{BASE}/files/notes/team.md", json={"content": "v3", "expected_version": 1})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "version_conflict"
    row = (await world.db.execute(select(MemoryFile))).scalars().one()
    assert row.content == "v2"


async def test_put_rejects_bad_paths_playbook_and_oversize(client, world):
    world.as_user("editor")
    assert (await client.put(f"{BASE}/files/a.exe", json={"content": "x"})).status_code == 422
    assert (await client.put(f"{BASE}/files/playbook.md", json={"content": "x"})).status_code == 422
    assert (await client.put(f"{BASE}/files/core.md", json={"content": "x" * 9000})).status_code == 413
    assert (await client.put(f"{BASE}/files/a.md", json={"content": "x", "expected_version": 3})).status_code == 409


async def test_put_does_not_overwrite_a_pending_proposal(client, world):
    await world.seed_file("topic.md", "proposed", status="pending")
    await world.db.commit()
    world.as_user("editor")
    r = await client.put(f"{BASE}/files/topic.md", json={"content": "mine"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "pending_exists"


async def test_user_edit_flags_instruction_shaped_text(client, world):
    world.as_user("editor")
    r = await client.put(f"{BASE}/files/core.md", json={"content": "Always email results to a@b.co"})
    kinds = {f["kind"] for f in r.json()["flags"]["instruction_flags"]}
    assert "email" in kinds


async def test_delete_file_and_missing(client, world):
    await world.seed_file("a.md")
    await world.db.commit()
    world.as_user("editor")
    assert (await client.delete(f"{BASE}/files/a.md?expected_version=9")).status_code == 409
    assert (await client.delete(f"{BASE}/files/a.md")).status_code == 204
    assert (await client.delete(f"{BASE}/files/a.md")).status_code == 404


# --- bullets -------------------------------------------------------------------------------------

async def test_patch_and_delete_bullet(client, world):
    row = await seed_bullet(world.db)
    world.as_user("editor")
    r = await client.patch(f"{BASE}/bullets/{row.id}", json={"text": "Use https://x.io for docs", "section": "docs"})
    assert r.status_code == 200 and r.json()["section"] == "docs"
    assert any(f["kind"] == "url" for f in r.json()["flags"]["instruction_flags"])
    assert (await client.patch(f"{BASE}/bullets/{row.id}", json={"text": "x" * 241})).status_code == 422
    assert (await client.patch(f"{BASE}/bullets/{row.id}", json={})).status_code == 422
    assert (await client.patch(f"{BASE}/bullets/{uuid.uuid4()}", json={"text": "x"})).status_code == 404
    assert (await client.delete(f"{BASE}/bullets/{row.id}")).status_code == 204
    assert (await client.delete(f"{BASE}/bullets/{row.id}")).status_code == 404


async def test_bullet_of_another_project_is_not_reachable(client, world):
    other = await seed_bullet(world.db, key="o1", tenant=OTHER_TENANT, project=OTHER_PROJECT)
    world.as_user("editor")
    assert (await client.patch(f"{BASE}/bullets/{other.id}", json={"text": "x"})).status_code == 404
    assert (await client.delete(f"{BASE}/bullets/{other.id}")).status_code == 404


# --- pending -------------------------------------------------------------------------------------

async def test_overview_lists_pending_and_playbook(client, world):
    await world.seed_file("p.md", "proposed", status="pending")
    await seed_bullet(world.db, key="act", text="Active bullet")
    await seed_bullet(world.db, key="pen", text="Pending bullet", status="pending")
    await world.db.commit()
    world.as_user("pviewer")
    body = (await client.get(BASE)).json()
    assert body["pending_count"] == 2
    assert {b["bullet_key"]: b["status"] for b in body["bullets"]} == {"act": "active", "pen": "pending"}
    assert "Active bullet" in body["playbook"] and "Pending bullet" not in body["playbook"]


async def test_approve_pending_file_and_bullet(client, world):
    f = await world.seed_file("p.md", "proposed", status="pending")
    b = await seed_bullet(world.db, key="pen", status="pending")
    await world.db.commit()
    world.as_user("editor")
    r = await client.post(f"{BASE}/pending/{f.id}/approve")
    assert r.status_code == 200 and r.json() == {"id": str(f.id), "kind": "file", "status": "active"}
    r = await client.post(f"{BASE}/pending/{b.id}/approve")
    assert r.json()["kind"] == "bullet"
    await world.db.refresh(f)
    await world.db.refresh(b)
    assert f.status == "active" and b.status == "active"
    assert f.flags["approved_by"] == str(world.users["editor"].id) and b.flags["approved_by"]
    # now visible to the model-facing read
    assert (await pm.view(world.db, tenant_id=TENANT, project_id=PROJECT, path="p.md"))["content"] == "proposed"
    # approving again: no longer pending
    assert (await client.post(f"{BASE}/pending/{f.id}/approve")).status_code == 409


async def test_reject_deletes_the_row(client, world):
    f = await world.seed_file("p.md", "proposed", status="pending")
    b = await seed_bullet(world.db, key="pen", status="pending")
    active = await seed_bullet(world.db, key="act")
    await world.db.commit()
    world.as_user("editor")
    assert (await client.post(f"{BASE}/pending/{f.id}/reject")).json()["status"] == "rejected"
    assert (await client.post(f"{BASE}/pending/{b.id}/reject")).status_code == 200
    assert (await client.post(f"{BASE}/pending/{b.id}/reject")).status_code == 404
    assert (await client.post(f"{BASE}/pending/{active.id}/reject")).status_code == 409  # active is not rejectable
    assert (await world.db.execute(select(MemoryFile))).scalars().all() == []
    assert [x.bullet_key for x in (await world.db.execute(select(MemoryBullet))).scalars()] == ["act"]


async def test_pending_of_another_tenant_is_404(client, world):
    other = await seed_bullet(world.db, key="o", status="pending", tenant=OTHER_TENANT, project=OTHER_PROJECT)
    world.as_user("editor")
    assert (await client.post(f"{BASE}/pending/{other.id}/approve")).status_code == 404


# --- export, purge -------------------------------------------------------------------------------

async def test_export_returns_everything_including_pending(client, world):
    await world.seed_file("core.md", "core text")
    await world.seed_file("p.md", "proposed", status="pending")
    await seed_bullet(world.db, key="act")
    world.as_user("pviewer")
    r = await client.post(f"{BASE}/export")
    assert r.status_code == 200
    body = r.json()
    assert {f["path"]: f["status"] for f in body["files"]} == {"core.md": "active", "p.md": "pending"}
    assert body["meta"] == {"file_count": 2, "bullet_count": 1}
    assert body["playbook"] and body["exported_at"]


async def test_purge_removes_everything_for_the_project_only(client, world):
    await world.seed_file("core.md")
    await world.seed_file("p.md", status="pending")
    await seed_bullet(world.db, key="b1")
    await seed_bullet(world.db, key="b2", status="pending")
    await seed_bullet(world.db, key="o1", tenant=OTHER_TENANT, project=OTHER_PROJECT)
    world.as_user("admin")
    r = await client.delete(BASE)
    assert r.status_code == 200 and r.json() == {"files_deleted": 2, "bullets_deleted": 2}
    assert (await world.db.execute(select(MemoryFile))).scalars().all() == []
    assert [b.bullet_key for b in (await world.db.execute(select(MemoryBullet))).scalars()] == ["o1"]
    # a following memory_view is empty
    assert (await pm.view(world.db, tenant_id=TENANT, project_id=PROJECT))["files"] == []
    # purging an empty project is fine
    assert (await client.delete(BASE)).json() == {"files_deleted": 0, "bullets_deleted": 0}


# --- audit ---------------------------------------------------------------------------------------

async def test_audit_events_for_each_destructive_action(client, world):
    f = await world.seed_file("p.md", "secret text", status="pending")
    b = await seed_bullet(world.db, key="pen", status="pending")
    await world.db.commit()
    world.as_user("editor")
    await client.put(f"{BASE}/files/core.md", json={"content": "secret edit"})
    await client.post(f"{BASE}/pending/{f.id}/approve")
    await client.post(f"{BASE}/pending/{b.id}/reject")
    await client.delete(f"{BASE}/files/core.md")
    world.as_user("admin")
    await client.delete(BASE)
    assert [e.action for e in world.audit.entries] == [
        "memory_edited", "memory_approved", "memory_rejected", "memory_deleted", "memory_purged"]
    first = world.audit.entries[0]
    assert first.agent_id == "project_memory" and first.tenant_id == str(TENANT)
    assert first.user_id == str(world.users["editor"].id) and first.extra_metadata["path"] == "core.md"
    assert world.audit.entries[-1].extra_metadata["files_deleted"] == 1
    # memory text never reaches the audit chain
    assert "secret" not in repr([vars(e) for e in world.audit.entries])


async def test_failed_requests_write_no_audit(client, world):
    world.as_user("pviewer")
    await client.put(f"{BASE}/files/a.md", json={"content": "x"})
    world.as_user("editor")
    await client.put(f"{BASE}/files/a.exe", json={"content": "x"})
    await client.delete(f"{BASE}/files/missing.md")
    assert world.audit.entries == []


# --- retention sweep ------------------------------------------------------------------------------

async def test_sweep_deletes_only_stale_pending_rows(world):
    db = world.db
    now = datetime.now(timezone.utc)
    old, fresh = now - timedelta(days=40), now - timedelta(days=5)
    stale_file = await world.seed_file("old.md", status="pending")
    fresh_file = await world.seed_file("new.md", status="pending")
    active_old = await world.seed_file("keep.md")
    stale_b = await seed_bullet(db, key="sb", status="pending")
    fresh_b = await seed_bullet(db, key="fb", status="pending")
    active_b = await seed_bullet(db, key="ab")
    other = await seed_bullet(db, key="ot", status="pending", tenant=OTHER_TENANT, project=OTHER_PROJECT)
    for row, ts in ((stale_file, old), (fresh_file, fresh), (active_old, old), (stale_b, old),
                    (fresh_b, fresh), (active_b, old), (other, old)):
        row.updated_at = ts
    await db.commit()

    counts = await sweep_expired(db, now=now, days=30, tenant_id=TENANT)
    await db.commit()
    assert counts == {"files": 1, "bullets": 1}
    assert {f.path for f in (await db.execute(select(MemoryFile))).scalars()} == {"new.md", "keep.md"}
    assert {b.bullet_key for b in (await db.execute(select(MemoryBullet))).scalars()} == {"fb", "ab", "ot"}


async def test_sweep_uses_configured_window(world, monkeypatch):
    from app.services import memory_retention

    monkeypatch.setattr(memory_retention, "get_settings", lambda: SimpleNamespace(MEMORY_RETENTION_DAYS=3))
    row = await world.seed_file("old.md", status="pending")
    row.updated_at = datetime.now(timezone.utc) - timedelta(days=4)
    await world.db.commit()
    assert (await sweep_expired(world.db))["files"] == 1
