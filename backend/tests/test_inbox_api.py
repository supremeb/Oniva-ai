"""U7 - /api/v1/inbox routes: auth, response shape, validation."""
import httpx
import pytest
from fastapi import FastAPI

from app.api.v1 import inbox
from app.auth.dependencies import get_current_user
from app.core.database import get_db
from app.services import approval_service as aps
from app.services.engine_client import get_engine_client
from tests.test_approval_service import FakeEngine, auth, world  # noqa: F401  (fixtures)


@pytest.fixture
async def client(world):
    aps.reset_reconcile_throttle()
    app = FastAPI()
    app.include_router(inbox.router, prefix="/api/v1")

    async def _db():
        yield world.db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_engine_client] = lambda: FakeEngine()
    world.as_user = lambda name: app.dependency_overrides.__setitem__(
        get_current_user, lambda: auth(world.users[name]))
    world.app = app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_requires_authentication(client):
    assert (await client.get("/api/v1/inbox")).status_code in (401, 403)
    assert (await client.get("/api/v1/inbox/count")).status_code in (401, 403)


async def test_list_shape_and_count(client, world):
    row = await world.pending(requested_by="member")
    world.as_user("member")
    r = await client.get("/api/v1/inbox")
    assert r.status_code == 200
    body = r.json()
    assert body["nextCursor"] is None and len(body["items"]) == 1
    item = body["items"][0]
    assert item["kind"] == "approval" and item["source"] == {"type": "tool_approval", "id": str(row.id)}
    assert item["actions"][0]["type"] == "decide" and "_created" not in item
    c = await client.get("/api/v1/inbox/count")
    assert c.json()["total"] == 1 and c.json()["byKind"]["approval"] == 1


async def test_limit_above_200_is_422_and_bad_inputs(client, world):
    world.as_user("member")
    assert (await client.get("/api/v1/inbox?limit=201")).status_code == 422
    assert (await client.get("/api/v1/inbox?limit=0")).status_code == 422
    assert (await client.get("/api/v1/inbox?kinds=bogus")).status_code == 422
    assert (await client.get("/api/v1/inbox?cursor=garbage")).status_code == 422
    assert (await client.get("/api/v1/inbox?limit=200&kinds=approval,fleet")).status_code == 200


async def test_paging_through_the_route(client, world):
    for _ in range(3):
        await world.pending(requested_by="member")
    world.as_user("member")
    first = (await client.get("/api/v1/inbox?limit=2")).json()
    assert len(first["items"]) == 2 and first["nextCursor"]
    second = (await client.get("/api/v1/inbox", params={"limit": 2, "cursor": first["nextCursor"]})).json()
    assert len(second["items"]) == 1 and second["nextCursor"] is None
