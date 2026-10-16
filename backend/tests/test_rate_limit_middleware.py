"""RateLimitMiddleware: over-limit requests get a real 429, and engine service traffic is not IP-limited."""

import httpx
import pytest
from fastapi import FastAPI

from app.middleware.rate_limit import RateLimitMiddleware


class _AlwaysOver:
    """A limiter that has already used the whole window."""

    async def is_allowed(self, key, limit_override=None):
        return False, {"limit": 1, "remaining": 0, "reset": 0}

    async def acquire_sse_slot(self, user_id):
        return False

    async def release_sse_slot(self, user_id):
        return None


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/things")
    async def things():
        return {"ok": True}

    @app.post("/internal/models/policy")
    async def policy():
        return {"ok": True}

    mw = RateLimitMiddleware(app, enabled=True)
    mw.limiter = _AlwaysOver()
    return mw


async def _get(app, method: str, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request(method, path)


@pytest.mark.asyncio
async def test_over_limit_is_a_429_with_retry_after_not_a_500():
    r = await _get(_app(), "GET", "/api/v1/things")
    assert r.status_code == 429
    assert r.json()["detail"] == "Rate limit exceeded. Please slow down."
    assert r.headers["retry-after"] == "1" and r.headers["x-ratelimit-remaining"] == "0"


@pytest.mark.asyncio
async def test_internal_service_routes_are_not_ip_rate_limited():
    # The engine reaches every /internal route from one address; those routes carry their own bearer
    # auth and per-tenant tool limits, so the per-IP browser budget must not throttle them.
    r = await _get(_app(), "POST", "/internal/models/policy")
    assert r.status_code == 200 and r.json() == {"ok": True}
