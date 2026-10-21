"""Regression tests for the QA round 2 findings."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models.tenant import Tenant
from tests.platform_app import TENANT, client_for, make_app, make_user


def _audit_row(state):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(), agent_id="a", decision="d", rationale="r", data_sources=None,
        workflow_state=state, workflow_id=None, session_id=None, user_id=None, tenant_id=TENANT,
        created_at=now, signature="s", previous_hash=None,
    )


async def test_compliance_logs_lists_rows_with_null_workflow_state(test_db, monkeypatch):
    """Approval/policy audit rows have no workflow_state; the entry model must accept that."""
    from app.api.v1 import audit
    from app.services.audit_logger import _row_to_entry

    class _Logger:
        async def query_logs(self, **kw):
            return [_row_to_entry(_audit_row(s)) for s in (None, "running")]

    monkeypatch.setattr(audit, "get_audit_logger", lambda db: _Logger())
    app = make_app(test_db, make_user("owner"), audit.router, prefix="/api/v1/audit")
    async with client_for(app) as c:
        r = await c.get("/api/v1/audit/compliance/logs")
        assert r.status_code == 200, r.text
        assert len(r.json()) == 2


# --- 2. Role hierarchy ------------------------------------------------------------------------

def _role_checkers(router):
    """Every require_role checker on every route of ``router`` (route-level and router-level)."""
    found = []

    def walk(dependant):
        for d in dependant.dependencies:
            if getattr(d.call, "__name__", "") == "role_checker":
                found.append(d.call)
            walk(d)

    for route in router.routes:
        if hasattr(route, "dependant"):
            walk(route.dependant)
    return found


ROLE_GATED_ROUTERS = [
    "audit", "admin", "observability_api", "llm_usage", "llm_providers",
    "routing_policies", "users", "pending_users", "process_logs",
]


@pytest.mark.parametrize("name", ROLE_GATED_ROUTERS)
async def test_every_role_gate_admits_owner_and_admin_and_rejects_member(name):
    import importlib

    router = importlib.import_module(f"app.api.v1.{name}").router
    checkers = _role_checkers(router)
    assert checkers, f"{name} has no require_role gate"
    for check in checkers:
        for role in ("owner", "admin"):
            assert (await check(user=make_user(role))).role == role
        with pytest.raises(HTTPException) as exc:
            await check(user=make_user("member"))
        assert exc.value.status_code == 403
        with pytest.raises(HTTPException):
            await check(user=make_user("viewer"))


def test_role_satisfies_is_a_minimum_not_a_set():
    from app.auth.dependencies import role_satisfies

    assert role_satisfies("owner", ("admin",))
    assert role_satisfies("admin", ("member",))
    assert role_satisfies("member", ("member",))
    assert not role_satisfies("admin", ("owner",))
    assert not role_satisfies("viewer", ("member",))
    assert not role_satisfies("stranger", ("viewer",))
    assert role_satisfies("custom", ("custom",))


def test_permissions_are_inherited_down_the_hierarchy():
    from app.auth.permissions import has_permission

    assert has_permission("owner", "anything:at_all")
    assert has_permission("admin", "users:manage")
    assert has_permission("admin", "workflows:delete_own")  # member permission
    assert not has_permission("member", "users:manage")
    assert not has_permission("viewer", "workflows:create")


async def test_owner_can_write_routing_policies_and_both_url_forms_work(test_db):
    from app.api.v1 import routing_policies

    test_db.add(Tenant(id=TENANT, name="t", slug="t", settings={}))
    await test_db.commit()
    body = {"name": "p", "task_type": "browser", "model_id": "auto", "provider": "auto", "priority": 0}
    for role, expected in (("owner", 201), ("admin", 201), ("member", 403)):
        app = make_app(test_db, make_user(role), routing_policies.router, prefix="/api/v1/routing-policies")
        async with client_for(app) as c:
            r = await c.post("/api/v1/routing-policies", json=body)
            assert r.status_code == expected, (role, r.text)
            r = await c.post("/api/v1/routing-policies/", json=body)
            assert r.status_code == expected, (role, r.text)
    app = make_app(test_db, make_user("member"), routing_policies.router, prefix="/api/v1/routing-policies")
    async with client_for(app) as c:
        a = await c.get("/api/v1/routing-policies")
        b = await c.get("/api/v1/routing-policies/")
        assert a.status_code == b.status_code == 200
        assert a.json() == b.json() and len(a.json()) == 4


# --- 3. KB upload type ------------------------------------------------------------------------

async def test_kb_upload_unsupported_type_is_415(test_db, monkeypatch):
    from app.api.v1 import knowledge_management
    from app.models.knowledge_base import KnowledgeBase

    kb = KnowledgeBase(tenant_id=TENANT, name="kb", source_type="manual", created_by=uuid.uuid4())
    test_db.add(kb)
    await test_db.commit()
    app = make_app(test_db, make_user("owner"), knowledge_management.router)

    @app.middleware("http")
    async def _state(request, call_next):
        request.state.tenant_id = str(TENANT)
        return await call_next(request)

    async with client_for(app) as c:
        r = await c.post(f"/api/v1/knowledge/bases/{kb.id}/documents", files={"file": ("evil.exe", b"MZ")})
        assert r.status_code == 415, r.text
        assert "Unsupported file type" in r.json()["detail"]


# --- 4. Timestamps ----------------------------------------------------------------------------

def test_every_utc_column_default_is_timezone_aware():
    """A naive datetime bound to timestamptz is read in the server's local zone (hours of skew)."""
    from sqlalchemy import DateTime

    from app.models import Base

    bad = []
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if not (isinstance(col.type, DateTime) and col.type.timezone):
                continue
            for kind, d in (("default", col.default), ("onupdate", col.onupdate)):
                if d is None or not getattr(d, "is_callable", False):
                    continue
                try:
                    value = d.arg(None)
                except TypeError:
                    value = d.arg()
                if isinstance(value, datetime) and value.tzinfo is None:
                    bad.append(f"{table.name}.{col.name} {kind}")
    assert not bad, bad


# --- 5. check-email ---------------------------------------------------------------------------

async def test_check_email_distinguishes_existing_account_from_unknown(test_db):
    from app.api.v1 import auth
    from app.core.database import get_db_no_tenant
    from app.models.user import User

    test_db.add(Tenant(id=TENANT, name="t", slug="t", settings={}))
    test_db.add(User(id=uuid.uuid4(), tenant_id=TENANT, email="here@x.io", name="h", role="member", is_active=True))
    await test_db.commit()
    app = make_app(test_db, make_user(), auth.router, prefix="/api/v1/auth")

    async def _db():
        yield test_db

    app.dependency_overrides[get_db_no_tenant] = _db
    async with client_for(app) as c:
        r = (await c.get("/api/v1/auth/check-email/Here@x.io")).json()
        assert r["can_signup"] is False and "already exists" in r["message"]
        r = (await c.get("/api/v1/auth/check-email/ghost@x.io")).json()
        assert r["can_signup"] is False and "not found" in r["message"].lower()


# --- 7. Retry-After ---------------------------------------------------------------------------

async def test_lockout_retry_after_is_the_remaining_lock_time():
    from app.auth.login_lockout import LoginLockout
    from tests.security.test_platform_rbac import FakeRedis

    redis = FakeRedis()
    lock = LoginLockout(redis, max_failures=2, lockout_seconds=900)
    await lock.record_failure("a@x.io")
    await lock.record_failure("a@x.io")
    redis.ttls[lock._key("a@x.io")] = 421
    assert await lock.retry_after("a@x.io") == 421


def test_cors_exposes_retry_after_to_browsers():
    from fastapi.middleware.cors import CORSMiddleware

    from app.main import app

    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    assert "Retry-After" in cors.kwargs["expose_headers"]


# --- 8. Write-then-read -----------------------------------------------------------------------

async def test_write_routes_commit_before_the_response_is_sent(monkeypatch):
    """Dependencies with ``yield`` tear down after the response is sent by default, so a client
    that signs up and immediately logs in raced the commit. Auth/user/invite writes now use
    ``scope="function"`` so the commit lands first."""
    from app.api.v1 import auth
    from app.core.database import get_db_no_tenant

    events = []

    async def _db():
        yield MagicMock()
        events.append("commit")

    class _Svc:
        def __init__(self, db):
            pass

        async def signup_user(self, **kw):
            return SimpleNamespace(id=uuid.uuid4(), email="n@x.io", name="n", role="member",
                                   tenant_id=TENANT)

    monkeypatch.setattr(auth, "UserManagementService", _Svc)
    app = make_app(None, make_user(), auth.router, prefix="/api/v1/auth")
    app.dependency_overrides[get_db_no_tenant] = _db
    body = b'{"email":"n@x.io","password":"Zq7!mXp#4vLk9w","name":"n"}'
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "path": "/api/v1/auth/signup", "raw_path": b"/api/v1/auth/signup", "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        "client": ("t", 1), "server": ("t", 80), "scheme": "http", "root_path": "",
    }
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    status_seen = []

    async def send(message):
        if message["type"] == "http.response.start":
            status_seen.append(message["status"])
            events.append("response_start")

    await app(scope, receive, send)
    assert status_seen == [200]
    assert events == ["commit", "response_start"]


async def test_signup_then_immediate_login_succeeds(test_db, monkeypatch):
    from app.api.v1 import auth
    from app.auth.login_lockout import LoginLockout
    from app.core.database import get_db_no_tenant
    from app.models.pending_user import PendingUser
    from tests.security.test_platform_rbac import FakeRedis

    monkeypatch.setattr(auth, "login_lockout", LoginLockout(FakeRedis()))
    test_db.add(Tenant(id=TENANT, name="t", slug="t", settings={}))
    test_db.add(PendingUser(tenant_id=TENANT, email="new@x.io", role="member"))
    await test_db.commit()
    app = make_app(test_db, make_user(), auth.router, prefix="/api/v1/auth")

    async def _db():
        yield test_db

    app.dependency_overrides[get_db_no_tenant] = _db
    password = "Zq7!mXp#4vLk9w"
    async with client_for(app) as c:
        r = await c.post("/api/v1/auth/signup", json={"email": "New@x.io", "password": password, "name": "N"})
        assert r.status_code == 200, r.text
        for _ in range(3):  # every attempt succeeds at once, no retry needed
            r = await c.post("/api/v1/auth/login", json={"email": "new@x.io", "password": password})
            assert r.status_code == 200, r.text
            assert r.json()["user"]["email"] == "new@x.io"
