"""Regression tests for 5xx errors found in the platform QA pass."""
import uuid
from types import SimpleNamespace

import pytest

from app.api.v1 import knowledge_management, projects, routing_policies, volumes
from app.models.tenant import Tenant
from app.models.user import User
from app.services.project_service import ProjectService
from app.services.volumes import VolumeManager, get_volume_manager
from tests.platform_app import TENANT, client_for, make_app, make_user


async def test_volumes_pass_tenant(test_db, tmp_path):
    app = make_app(test_db, make_user(), volumes.router)
    app.dependency_overrides[get_volume_manager] = lambda: VolumeManager(base_path=tmp_path)
    (tmp_path / str(TENANT) / "vol1").mkdir(parents=True)
    (tmp_path / str(TENANT) / "vol1" / "a.txt").write_text("hi")
    async with client_for(app) as c:
        r = await c.get("/api/v1/volumes")
        assert r.status_code == 200, r.text
        assert [v["name"] for v in r.json()["volumes"]] == ["vol1"]
        assert (await c.get("/api/v1/volumes/vol1")).status_code == 200
        assert (await c.get("/api/v1/volumes/vol1/files")).status_code == 200
        assert (await c.get("/api/v1/volumes/vol1/files/a.txt")).text == "hi"
        assert (await c.get("/api/v1/volumes/sessions/s1/mounts")).status_code == 200
        assert (await c.get("/api/v1/volumes/nope")).status_code == 404


async def test_tenant_feature_and_approval_writes_use_system_session(test_db, monkeypatch):
    from app.api.v1 import tenant_settings as ts
    from app.core.database import get_system_db
    from tests.test_approval_service import FakeAudit

    test_db.add(Tenant(id=TENANT, name="t", slug="t", settings={}))
    await test_db.commit()
    used = []

    async def _sys():
        used.append(1)
        yield test_db

    monkeypatch.setattr(ts, "get_audit_chain", lambda db: FakeAudit())
    app = make_app(test_db, make_user("owner"), ts.router)
    app.dependency_overrides[get_system_db] = _sys
    flag = next(f for f in ts.FLAG_METADATA
                if not ts.is_operator_only(f) and not ts.validate_flag_dependencies(f, False, Tenant(settings={})))
    async with client_for(app) as c:
        r = await c.put(f"/api/v1/tenant/features/{flag}", json={"enabled": False})
        assert r.status_code == 200, r.text
        assert r.json()["features"][flag] is False
        r = await c.put("/api/v1/tenant/approvals", json={"ttl_hours": 12})
        assert r.status_code == 200, r.text
        assert r.json()["ttl_hours"] == 12
    assert len(used) == 2


async def test_routing_policies_crud(test_db):
    test_db.add(Tenant(id=TENANT, name="t", slug="t", settings={}))
    await test_db.commit()
    app = make_app(test_db, make_user("admin"), routing_policies.router, prefix="/api/v1/routing-policies")
    async with client_for(app) as c:
        assert (await c.get("/api/v1/routing-policies/")).status_code == 200
        body = {"name": "n", "task_type": "research", "model_id": "m", "provider": "openai"}
        r = await c.post("/api/v1/routing-policies/", json=body)
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        assert (await c.put(f"/api/v1/routing-policies/{pid}", json={"name": "n2"})).status_code == 200
        assert (await c.delete(f"/api/v1/routing-policies/{pid}")).status_code == 204


# -- projects: members + file upload ------------------------------------------
@pytest.fixture
async def proj(test_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # document storage writes under ./data

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(projects, "_process_project_doc_in_background", _noop)
    test_db.add(Tenant(id=TENANT, name="t", slug="t", settings={}))
    owner = User(id=uuid.uuid4(), tenant_id=TENANT, email="o@x.io", name="o", role="owner", is_active=True)
    other = User(id=uuid.uuid4(), tenant_id=TENANT, email="m@x.io", name="m", role="member", is_active=True)
    test_db.add_all([owner, other])
    await test_db.commit()
    p = await ProjectService(test_db).create_project(tenant_id=TENANT, user_id=owner.id, name="P")
    await test_db.commit()
    app = make_app(test_db, make_user("owner", user_id=owner.id), projects.router, prefix="/api/v1/projects")
    app.include_router(knowledge_management.router, prefix="/api/v1")
    return SimpleNamespace(app=app, id=p.id, other=other)


async def test_add_member_duplicate_409_unknown_404(proj):
    async with client_for(proj.app) as c:
        url = f"/api/v1/projects/{proj.id}/members"
        body = {"user_id": str(proj.other.id), "role": "viewer"}
        assert (await c.post(url, json=body)).status_code == 201
        assert (await c.post(url, json=body)).status_code == 409
        r = await c.post(url, json={"user_id": str(uuid.uuid4()), "role": "viewer"})
        assert r.status_code == 404


async def test_project_upload_unsupported_type_is_415_and_nothing_stored(proj, tmp_path):
    async with client_for(proj.app) as c:
        r = await c.post(f"/api/v1/projects/{proj.id}/files", files={"file": ("evil.exe", b"MZ")})
        assert r.status_code == 415, r.text
        assert not (tmp_path / "data").exists()
        r = await c.get(f"/api/v1/projects/{proj.id}/files")
        assert r.json()["files"] == []


async def test_project_upload_ok_returns_201(proj):
    async with client_for(proj.app) as c:
        r = await c.post(f"/api/v1/projects/{proj.id}/files", files={"file": ("a.txt", b"hello")})
        assert r.status_code == 201, r.text
        assert r.json()["filename"] == "a.txt"


async def test_kb_upload_returns_201(proj, test_db, monkeypatch):
    from app.models.knowledge_base import KnowledgeBase
    kb = KnowledgeBase(tenant_id=TENANT, name="kb", source_type="manual", created_by=uuid.uuid4())
    test_db.add(kb)
    await test_db.commit()

    # the background processing task is best-effort and needs a real tenant DB: don't run it here
    monkeypatch.setattr(knowledge_management.asyncio, "create_task", lambda coro: coro.close())
    # The route reads the tenant from request.state (set by the real middleware).
    async def _state(request, call_next):
        request.state.tenant_id = str(TENANT)
        return await call_next(request)

    proj.app.middleware("http")(_state)
    async with client_for(proj.app) as c:
        r = await c.post(f"/api/v1/knowledge/bases/{kb.id}/documents", files={"file": ("a.txt", b"hello")})
        assert r.status_code == 201, r.text


async def test_background_jobs_create_validate_and_cancel(proj, test_db):
    from sqlalchemy import func, select
    from app.api.v1 import background_jobs
    from app.models.background_job import BackgroundJob
    proj.app.include_router(background_jobs.router, prefix="/api/v1/background-jobs")
    async with client_for(proj.app) as c:
        r = await c.post("/api/v1/background-jobs/", json={"job_type": "background_agent"})
        assert r.status_code == 422
        n = (await test_db.execute(select(func.count()).select_from(BackgroundJob))).scalar()
        assert n == 0  # nothing written
        r = await c.post("/api/v1/background-jobs/", json={"job_type": "batch_processing"})
        assert r.status_code == 201, r.text
        r = await c.post(f"/api/v1/background-jobs/{r.json()['id']}/cancel", json={})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "cancelled"


def test_tenant_context_is_reapplied_per_transaction():
    from unittest.mock import MagicMock
    from app.core import database as dbm
    conn = MagicMock()
    dbm._reapply_tenant_context(SimpleNamespace(info={}), None, conn)
    conn.execute.assert_not_called()  # system / non-tenant sessions are untouched
    dbm._reapply_tenant_context(SimpleNamespace(info={dbm._TENANT_INFO_KEY: str(TENANT)}), None, conn)
    assert conn.execute.call_args.args[1] == {"tenant_id": str(TENANT)}


async def test_set_tenant_context_remembers_tenant_on_session():
    from unittest.mock import AsyncMock
    from app.core import database as dbm
    s = AsyncMock()
    s.info = {}
    await dbm.set_tenant_context(s, str(TENANT))
    assert s.info[dbm._TENANT_INFO_KEY] == str(TENANT)


async def test_skills_validation_and_system_skill_detail(test_db):
    from app.api.v1 import skills
    app = make_app(test_db, make_user("member"), skills.router, prefix="/api/v1/skills")
    async with client_for(app) as c:
        assert (await c.get("/api/v1/skills?skip=-1")).status_code == 422
        assert (await c.get("/api/v1/skills?limit=0")).status_code == 422
        listed = (await c.get("/api/v1/skills")).json()
        assert listed
        for s in listed[:5]:
            r = await c.get(f"/api/v1/skills/{s['id']}")
            assert r.status_code == 200 and r.json()["name"] == s["name"]
        assert (await c.get(f"/api/v1/skills/{uuid.uuid4()}")).status_code == 404


async def test_browse_models_route_not_captured_by_provider_id(test_db):
    from app.api.v1 import llm_providers
    # browse-models is admin-only (provider writes and catalog browsing are admin settings)
    app = make_app(test_db, make_user("admin"), llm_providers.router, prefix="/api/v1/settings")
    async with client_for(app) as c:
        r = await c.get("/api/v1/settings/llm-providers/browse-models?provider_type=bogus")
        assert r.status_code == 400  # reached the handler (was 422 uuid-parse from /{provider_id})


async def test_cache_metrics_hit_rate_is_clamped():
    from unittest.mock import AsyncMock, MagicMock
    from datetime import datetime, timezone
    from app.api.v1.analytics import get_cache_metrics
    row = MagicMock(total_requests=2, cached_requests=2, total_cache_read=5000, total_cache_creation=0,
                    total_input=1000)  # providers that exclude cached tokens from input
    res = MagicMock()
    res.one.return_value = row
    db = AsyncMock()
    db.execute.return_value = res
    out = await get_cache_metrics(start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                                  end_date=datetime(2026, 2, 1, tzinfo=timezone.utc),
                                  db=db, user=MagicMock(), tenant_id=TENANT)
    assert out.hit_rate == 1.0


async def test_api_key_scopes_default_and_normalised(test_db):
    from app.models.api_key import APIKey, normalize_scopes
    from app.schemas.api_key import APIKeyBase
    assert normalize_scopes(["'*:*'"]) == ["*:*"]
    assert APIKeyBase(provider="p", name="n", scopes=["'*:*'"]).scopes == ["*:*"]
    assert APIKey(scopes=["'*:*'"]).has_scope("workflows:read")
    assert APIKey.__table__.c.scopes.default.arg(None) == ["*:*"]
    assert "'" not in APIKey.__table__.c.scopes.server_default.arg


async def test_kb_non_uuid_id_is_422(proj):
    async with client_for(proj.app) as c:
        for path in ("/api/v1/knowledge/bases/not-a-uuid", "/api/v1/knowledge/bases/not-a-uuid/documents"):
            assert (await c.get(path)).status_code == 422
