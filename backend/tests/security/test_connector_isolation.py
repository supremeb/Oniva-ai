"""U2 / R1 / R2 / AE1: connector API requires auth + RBAC + tenant ownership,
and credentials are bound per call and per tenant (no process-global client).
"""
import asyncio
import uuid
from typing import Any, Dict, List

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.auth.jwt import create_access_token
from app.connectors.base import BaseConnector, ConnectorMetadata
from app.connectors.registry import ConnectorRegistry
from app.core.database import get_db
from app.main import app
from app.models import Base
from app.models.connector import Connector

TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
USER_A = "a0000000-0000-0000-0000-000000000001"
USER_B = "b0000000-0000-0000-0000-000000000001"

AUTH_CALLS: List[Dict[str, Any]] = []


class FakeCrmConnector(BaseConnector):
    """Stateful on purpose: a shared instance would leak credentials across tenants."""

    metadata = ConnectorMetadata(
        id="fake_crm", name="Fake CRM", description="test", icon="", category="crm",
        auth_type="api_key", version="1.0.0",
    )

    def __init__(self):
        self.creds = None

    async def authenticate(self, credentials):
        AUTH_CALLS.append(dict(credentials))
        if credentials.get("api_key") == "reject":
            return False
        self.creds = dict(credentials)
        return True

    async def disconnect(self):
        self.creds = None
        return True

    async def health_check(self):
        return {"status": "healthy" if self.creds else "disconnected"}

    def get_tools(self):
        return [{"name": "whoami", "description": "echo key", "input_schema": {"type": "object"}}]

    async def call_tool(self, tool_name, arguments):
        await asyncio.sleep(0.01)  # force interleaving between concurrent callers
        return {"key": (self.creds or {}).get("api_key")}


def _token(tenant, user, role="admin"):
    return create_access_token(
        user_id=user, email=f"{user}@example.com", tenant_id=tenant, tenant_slug="t", role=role
    )


def _hdr(tenant, user, role="admin"):
    return {"Authorization": f"Bearer {_token(tenant, user, role)}"}



@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """Rate limiter and token blacklist talk to Redis; unit tier has none."""
    from app.auth import middleware as _mw
    from app.middleware.rate_limit import RateLimiter

    async def _allowed(self, identifier, limit_override=None):
        return True, {"limit": 1000, "remaining": 999, "reset": 0}

    async def _false(*a, **k):
        return False

    monkeypatch.setattr(RateLimiter, "is_allowed", _allowed)
    monkeypatch.setattr(_mw.token_blacklist, "is_blacklisted", _false)
    monkeypatch.setattr(_mw.token_blacklist, "is_user_invalidated", _false)


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def api(session_factory):
    AUTH_CALLS.clear()
    ConnectorRegistry.register(FakeCrmConnector)

    async def _get_db():
        async with session_factory() as s:
            yield s
            await s.commit()

    app.dependency_overrides[get_db] = _get_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(get_db, None)
    ConnectorRegistry._connectors.pop("fake_crm", None)


async def _rows(session_factory):
    async with session_factory() as s:
        return (await s.execute(select(Connector))).scalars().all()


# ---------------------------------------------------------------- AE1

async def test_ae1_unauthenticated_connect_is_401_and_changes_nothing(api, session_factory):
    body = {"credentials": {"api_key": "attacker-key"}}
    # No credentials at all.
    assert api.post("/api/v1/connectors/fake_crm/connect", json=body).status_code == 401
    # Bearer header skips CSRF, so this is the request that used to reach the handler.
    r = api.post(
        "/api/v1/connectors/fake_crm/connect", json=body, headers={"Authorization": "Bearer junk"}
    )
    assert r.status_code == 401
    assert AUTH_CALLS == []
    assert await _rows(session_factory) == []
    assert ConnectorRegistry.get_connector("fake_crm").creds is None


@pytest.mark.parametrize("path", ["", "fake_crm", "fake_crm/status", "fake_crm/tools"])
def test_read_routes_require_auth(api, path):
    assert api.get(f"/api/v1/connectors/{path}", headers={"Authorization": "Bearer junk"}).status_code == 401


# ---------------------------------------------------------------- RBAC

async def test_viewer_cannot_connect(api, session_factory):
    r = api.post(
        "/api/v1/connectors/fake_crm/connect",
        json={"credentials": {"api_key": "k"}},
        headers=_hdr(TENANT_A, USER_A, role="viewer"),
    )
    assert r.status_code == 403
    assert AUTH_CALLS == []
    assert await _rows(session_factory) == []


# ---------------------------------------------------------------- per-tenant storage

async def test_connect_stores_encrypted_creds_on_tenant_row_not_singleton(api, session_factory):
    r = api.post(
        "/api/v1/connectors/fake_crm/connect",
        json={"credentials": {"api_key": "key-A"}},
        headers=_hdr(TENANT_A, USER_A),
    )
    assert r.status_code == 200, r.text
    rows = await _rows(session_factory)
    assert len(rows) == 1
    assert str(rows[0].tenant_id) == TENANT_A
    assert rows[0].provider == "fake_crm"
    assert rows[0].encrypted_credentials and "key-A" not in rows[0].encrypted_credentials
    # No process-global client holds the credentials.
    assert ConnectorRegistry.get_connector("fake_crm").creds is None
    assert ConnectorRegistry.get_connector("fake_crm") is not ConnectorRegistry.get_connector("fake_crm")


async def test_failed_authentication_stores_nothing(api, session_factory):
    r = api.post(
        "/api/v1/connectors/fake_crm/connect",
        json={"credentials": {"api_key": "reject"}},
        headers=_hdr(TENANT_A, USER_A),
    )
    assert r.status_code == 400
    assert await _rows(session_factory) == []


async def test_tenant_b_cannot_see_tenant_a_connection(api, session_factory):
    api.post(
        "/api/v1/connectors/fake_crm/connect",
        json={"credentials": {"api_key": "key-A"}},
        headers=_hdr(TENANT_A, USER_A),
    )
    a = api.get("/api/v1/connectors/fake_crm/status", headers=_hdr(TENANT_A, USER_A)).json()
    b = api.get("/api/v1/connectors/fake_crm/status", headers=_hdr(TENANT_B, USER_B)).json()
    assert a["status"] == "healthy"
    assert b["status"] == "not_connected"


async def test_tenant_b_connect_does_not_overwrite_tenant_a(api, session_factory):
    api.post("/api/v1/connectors/fake_crm/connect", json={"credentials": {"api_key": "key-A"}},
             headers=_hdr(TENANT_A, USER_A))
    api.post("/api/v1/connectors/fake_crm/connect", json={"credentials": {"api_key": "key-B"}},
             headers=_hdr(TENANT_B, USER_B))
    rows = {str(r.tenant_id): r for r in await _rows(session_factory)}
    assert set(rows) == {TENANT_A, TENANT_B}

    from app.connectors.registry import decrypt_credentials
    assert decrypt_credentials(rows[TENANT_A].encrypted_credentials) == {"api_key": "key-A"}
    assert decrypt_credentials(rows[TENANT_B].encrypted_credentials) == {"api_key": "key-B"}


async def test_reconnect_updates_in_place(api, session_factory):
    for k in ("one", "two"):
        api.post("/api/v1/connectors/fake_crm/connect", json={"credentials": {"api_key": k}},
                 headers=_hdr(TENANT_A, USER_A))
    assert len(await _rows(session_factory)) == 1


def test_unknown_connector_is_404(api):
    r = api.post("/api/v1/connectors/nope/connect", json={"credentials": {}}, headers=_hdr(TENANT_A, USER_A))
    assert r.status_code == 404


# ---------------------------------------------------------------- per-call binding

async def test_concurrent_tenants_each_use_their_own_credentials(api, session_factory):
    for tenant, user, key in ((TENANT_A, USER_A, "key-A"), (TENANT_B, USER_B, "key-B")):
        assert api.post("/api/v1/connectors/fake_crm/connect", json={"credentials": {"api_key": key}},
                        headers=_hdr(tenant, user)).status_code == 200

    async def call(tenant):
        async with session_factory() as db:
            conn = await ConnectorRegistry.get_authenticated_connector("fake_crm", tenant, db=db)
            assert conn is not None
            return (await conn.call_tool("whoami", {}))["key"]

    results = await asyncio.gather(*[call(t) for t in [TENANT_A, TENANT_B] * 10])
    assert results == ["key-A", "key-B"] * 10


async def test_unconnected_tenant_gets_no_authenticated_connector(api, session_factory):
    api.post("/api/v1/connectors/fake_crm/connect", json={"credentials": {"api_key": "key-A"}},
             headers=_hdr(TENANT_A, USER_A))
    async with session_factory() as db:
        assert await ConnectorRegistry.get_authenticated_connector("fake_crm", TENANT_B, db=db) is None


async def test_invoke_connector_tool_skill_authenticates_per_call(api, session_factory, monkeypatch):
    """The chat path: authenticate() runs per call with the calling tenant's stored credentials."""
    from app.agents.skills import connectors as skill_mod
    from app.agents.skills.context import SkillContext

    for tenant, user, key in ((TENANT_A, USER_A, "key-A"), (TENANT_B, USER_B, "key-B")):
        api.post("/api/v1/connectors/fake_crm/connect", json={"credentials": {"api_key": key}},
                 headers=_hdr(tenant, user))
    AUTH_CALLS.clear()

    session_ctx = {"fake_crm": {"provider": "fake_crm", "name": "Fake CRM", "type": "native", "is_verified": True}}

    async def run(tenant, user):
        async with session_factory() as db:
            ctx = SkillContext(user_id=user, tenant_id=tenant, db=db, connectors=session_ctx)
            return await skill_mod.invoke_connector_tool(ctx, "fake_crm", "whoami", {})

    out = await asyncio.gather(run(TENANT_A, USER_A), run(TENANT_B, USER_B), run(TENANT_A, USER_A), run(TENANT_B, USER_B))
    assert [o["result"]["key"] for o in out] == ["key-A", "key-B", "key-A", "key-B"]
    assert {c["api_key"] for c in AUTH_CALLS} == {"key-A", "key-B"}


async def test_invoke_connector_tool_refuses_when_tenant_not_connected(api, session_factory):
    from app.agents.skills import connectors as skill_mod
    from app.agents.skills.context import SkillContext

    api.post("/api/v1/connectors/fake_crm/connect", json={"credentials": {"api_key": "key-A"}},
             headers=_hdr(TENANT_A, USER_A))
    async with session_factory() as db:
        ctx = SkillContext(
            user_id=USER_B, tenant_id=TENANT_B, db=db,
            connectors={"fake_crm": {"provider": "fake_crm", "name": "x", "type": "native"}},
        )
        out = await skill_mod.invoke_connector_tool(ctx, "fake_crm", "whoami", {})
    assert out["success"] is False
