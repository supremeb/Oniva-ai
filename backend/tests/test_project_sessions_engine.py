"""U2 / R2 / AE2: project sessions merge legacy chat sessions and engine conversations."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.exceptions import EntityNotFoundError
from app.models.chat_session import ChatSession
from app.models.engine import EngineConversation
from app.models.project import Project
from app.services.project_service import ProjectService


async def _project(db, tenant_id, owner_id) -> Project:
    project = Project(tenant_id=tenant_id, owner_id=owner_id, name="P")
    db.add(project)
    await db.flush()
    return project


def _legacy(db, project, tenant_id, user_id, title, when):
    db.add(ChatSession(
        tenant_id=tenant_id, user_id=user_id, project_id=project.id,
        title=title, messages=[], updated_at=when,
    ))


def _engine(db, project, tenant_id, user_id, title, when, archived_at=None) -> EngineConversation:
    conversation = EngineConversation(
        conversation_id=uuid.uuid4(), tenant_id=tenant_id, project_id=project.id,
        user_id=user_id, session_id="sess-1", title=title,
        updated_at=when, archived_at=archived_at,
    )
    db.add(conversation)
    return conversation


async def test_lists_legacy_and_engine_newest_first(test_db):
    t, u = uuid.uuid4(), uuid.uuid4()
    p = await _project(test_db, t, u)
    now = datetime.now(timezone.utc)
    _legacy(test_db, p, t, u, "old legacy", now - timedelta(hours=2))
    conv = _engine(test_db, p, t, u, "new engine", now - timedelta(hours=1))
    await test_db.commit()

    rows = await ProjectService(test_db).list_project_sessions(p.id, u, t)

    assert [r["title"] for r in rows] == ["new engine", "old legacy"]
    assert [r["source"] for r in rows] == ["engine", "legacy"]
    # Engine rows carry the ids the project page links with: /chat/projects/{project}?c={id}.
    assert rows[0]["id"] == str(conv.conversation_id)
    assert rows[0]["project_id"] == str(p.id)


async def test_archived_engine_conversations_are_excluded(test_db):
    t, u = uuid.uuid4(), uuid.uuid4()
    p = await _project(test_db, t, u)
    now = datetime.now(timezone.utc)
    _engine(test_db, p, t, u, "hidden", now - timedelta(hours=1), archived_at=now)
    _engine(test_db, p, t, u, "live", now - timedelta(hours=2))
    await test_db.commit()

    rows = await ProjectService(test_db).list_project_sessions(p.id, u, t)

    assert [r["title"] for r in rows] == ["live"]
    assert all(r["source"] == "engine" for r in rows)


async def test_other_tenant_and_other_user_rows_are_excluded(test_db):
    t, u = uuid.uuid4(), uuid.uuid4()
    other_t, other_u = uuid.uuid4(), uuid.uuid4()
    p = await _project(test_db, t, u)
    now = datetime.now(timezone.utc)
    _legacy(test_db, p, t, u, "mine", now - timedelta(hours=1))
    _legacy(test_db, p, other_t, u, "other tenant", now - timedelta(hours=2))
    _engine(test_db, p, t, other_u, "other user", now - timedelta(hours=3))
    await test_db.commit()

    rows = await ProjectService(test_db).list_project_sessions(p.id, u, t)

    assert [r["title"] for r in rows] == ["mine"]


async def test_without_project_access_raises_permission_error(test_db):
    t, u = uuid.uuid4(), uuid.uuid4()
    p = await _project(test_db, t, u)
    await test_db.commit()

    with pytest.raises(EntityNotFoundError):
        await ProjectService(test_db).list_project_sessions(p.id, uuid.uuid4(), t)


async def test_project_with_no_sessions_returns_empty_list(test_db):
    t, u = uuid.uuid4(), uuid.uuid4()
    p = await _project(test_db, t, u)
    await test_db.commit()

    assert await ProjectService(test_db).list_project_sessions(p.id, u, t) == []
