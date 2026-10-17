"""U3 - PUT /tenant/approvals: admins tune TTL and separate-approver, only owners the Rule of Two."""
import uuid

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api.v1 import tenant_settings as ts
from app.auth.dependencies import get_current_user
from app.auth.providers.base import AuthUser
from app.core.database import get_db
from app.models.tenant import Tenant
from tests.test_approval_service import FakeAudit

TENANT = uuid.uuid4()


@pytest.fixture
async def env(test_db, monkeypatch):
    test_db.add(Tenant(id=TENANT, name="t", slug="t", settings={"features": {"x": True}}))
    await test_db.commit()
    app = FastAPI()
    app.include_router(ts.router, prefix="/api/v1")

    async def _db():
        yield test_db

    class E:
        pass

    e = E()
    e.audit = FakeAudit()
    e.db = test_db
    e.role = "admin"
    monkeypatch.setattr(ts, "get_audit_chain", lambda db: e.audit)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: AuthUser(
        id=str(uuid.uuid4()), email="a@x.io", name="Ada", tenant_id=str(TENANT), tenant_slug="t",
        role=e.role, auth_provider="local")
    e.http = TestClient(app, raise_server_exceptions=False)
    return e


def test_defaults(env):
    r = env.http.get("/api/v1/tenant/approvals")
    assert r.json() == {"ttl_hours": 168, "require_separate_approver": False, "rule_of_two": True}


def test_admin_changes_ttl_and_separate_approver_keeping_other_settings(env):
    r = env.http.put("/api/v1/tenant/approvals", json={"ttl_hours": 24, "require_separate_approver": True})
    assert r.status_code == 200
    assert r.json() == {"ttl_hours": 24, "require_separate_approver": True, "rule_of_two": True}
    assert env.audit.entries == []


async def test_settings_persist_next_to_features(env):
    env.http.put("/api/v1/tenant/approvals", json={"ttl_hours": 0})
    tenant = await env.db.get(Tenant, TENANT)
    await env.db.refresh(tenant)
    assert tenant.settings["approvals"] == {"ttl_hours": 0}
    assert tenant.settings["features"] == {"x": True}


def test_admin_cannot_change_rule_of_two(env):
    r = env.http.put("/api/v1/tenant/approvals", json={"rule_of_two": False})
    assert r.status_code == 403
    assert env.audit.entries == []


def test_owner_changes_rule_of_two_and_it_is_audited_with_actor_snapshot(env):
    env.role = "owner"
    r = env.http.put("/api/v1/tenant/approvals", json={"rule_of_two": False})
    assert r.status_code == 200 and r.json()["rule_of_two"] is False
    [entry] = env.audit.entries
    assert entry.action == "rule_of_two_changed"
    assert (entry.actor_email, entry.actor_name) == ("a@x.io", "Ada")


def test_unchanged_rule_of_two_writes_no_entry(env):
    env.role = "owner"
    env.http.put("/api/v1/tenant/approvals", json={"rule_of_two": True})
    assert env.audit.entries == []


def test_member_and_invalid_values_are_refused(env):
    env.role = "member"
    assert env.http.put("/api/v1/tenant/approvals", json={"ttl_hours": 1}).status_code == 403
    env.role = "admin"
    assert env.http.put("/api/v1/tenant/approvals", json={"ttl_hours": -1}).status_code == 422
