"""GET /fleet/runs?status=<bogus> is a 422, like /background-jobs."""
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import fleet
from app.auth.dependencies import get_current_tenant_id, get_current_user
from app.core.database import get_db


def _client():
    app = FastAPI()
    app.include_router(fleet.router)
    tenant = uuid4()
    app.dependency_overrides[get_current_user] = lambda: MagicMock(tenant_id=str(tenant), id=str(uuid4()))
    app.dependency_overrides[get_current_tenant_id] = lambda: tenant
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    return TestClient(app)


def test_unknown_status_is_422():
    r = _client().get("/fleet/runs", params={"status": "bogus"})
    assert r.status_code == 422
    assert "status" in r.text
