"""Fleet/workflows on engine U1: automation project, run-conversation binding, `session_for`.

SQLite covers the idempotent paths; the concurrent create and migration 120 run on Postgres
(tests/postgres/test_automation_scope_pg.py).
"""
import uuid

from sqlalchemy import select

from app.config import Settings
from app.models.engine import EngineConversation
from app.models.project import Project
from app.models.tenant import Tenant
from app.models.user import User
from app.services import automation_scope as scope
from app.services.engine_client import get_engine_client
from app.services.engine_directory import EngineDirectory
from app.services.project_service import ProjectService
from tests.internal.test_invoke import maker, factory  # noqa: F401  (fixtures)

SECRET = "Zk3vN8qLw1RtYb6HdPx9CfUe2MaS5JoG7iVn4TlKq0A"


async def seed(maker, n=1):
    tenants, users = [], []
    async with maker() as db:
        for i in range(n):
            t, u = uuid.uuid4(), uuid.uuid4()
            db.add(Tenant(id=t, name=f"t{i}", slug=f"t{i}-{t.hex[:6]}", is_active=True, settings={}))
            tenants.append(t)
            users.append(u)
        await db.commit()
        for t, u in zip(tenants, users):
            db.add(User(id=u, tenant_id=t, email=f"{u.hex[:6]}@x.io", name="u"))
        await db.commit()
    return tenants, users


async def test_first_call_creates_the_project_and_the_second_reuses_it(maker, factory):
    (t,), (u,) = await seed(maker)
    first = await scope.ensure_automation_project(factory, t, u)
    second = await scope.ensure_automation_project(factory, t, u)
    assert first == second
    async with maker() as db:
        rows = (await db.execute(select(Project).where(Project.tenant_id == t))).scalars().all()
    assert len(rows) == 1 and rows[0].settings == {"system": "automations"} and rows[0].owner_id == u


async def test_tenants_get_separate_projects(maker, factory):
    (ta, tb), (ua, ub) = await seed(maker, 2)
    a = await scope.ensure_automation_project(factory, ta, ua)
    b = await scope.ensure_automation_project(factory, tb, ub)
    assert a != b


async def test_another_user_of_the_tenant_reuses_the_tenant_project(maker, factory):
    (t,), (u,) = await seed(maker)
    first = await scope.ensure_automation_project(factory, t, u)
    other = uuid.uuid4()
    async with maker() as db:
        db.add(User(id=other, tenant_id=t, email="o@x.io", name="o"))
        await db.commit()
    assert await scope.ensure_automation_project(factory, t, other) == first


async def test_bind_run_conversation_is_deterministic_and_writes_one_archived_row(maker, factory):
    (t,), (u,) = await seed(maker)
    ref = uuid.uuid4()
    a = await scope.bind_run_conversation(factory, "fleet", ref, t, u)
    b = await scope.bind_run_conversation(factory, "fleet", ref, t, u)
    assert a == b
    conversation_id, project_id, session_id = a
    assert conversation_id == uuid.uuid5(scope.RUN_NAMESPACE, f"fleet:{ref}")
    assert session_id == get_engine_client().directory.default_session_id
    async with maker() as db:
        rows = (await db.execute(select(EngineConversation))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.conversation_id == conversation_id and row.project_id == project_id
    assert (row.run_kind, row.run_ref_id) == ("fleet", ref)
    assert row.archived_at is not None and row.user_id == u and row.tenant_id == t


async def test_kind_is_part_of_the_conversation_id(maker, factory):
    (t,), (u,) = await seed(maker)
    ref = uuid.uuid4()
    f = await scope.bind_run_conversation(factory, "fleet", ref, t, u)
    w = await scope.bind_run_conversation(factory, "workflow", ref, t, u)
    assert f[0] != w[0]


async def test_automation_project_is_hidden_from_list_projects(maker, factory):
    (t,), (u,) = await seed(maker)
    await scope.ensure_automation_project(factory, t, u)
    async with maker() as db:
        db.add(Project(tenant_id=t, owner_id=u, name="Real", is_archived=False))
        await db.commit()
    async with maker() as db:
        out = await ProjectService(db).list_projects(u, t)
    assert [p["name"] for p in out["projects"]] == ["Real"] and out["total"] == 1


async def test_run_conversations_are_absent_from_the_chat_union(maker, factory):
    from app.api.v1.chat_sessions import _list_union

    (t,), (u,) = await seed(maker)
    await scope.bind_run_conversation(factory, "workflow", uuid.uuid4(), t, u)
    async with maker() as db:
        out = await _list_union(db, u, 1, 50)
    assert out.total == 0


def test_session_for_returns_the_default_session():
    d = EngineDirectory(Settings(_env_file=None, SECRET_KEY=SECRET, ENGINE_SESSION_ID="sess-9"))
    assert d.session_for(uuid.uuid4(), "fleet") == d.default_session_id == "sess-9"
