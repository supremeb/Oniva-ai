"""Shared harness for platform route tests: a FastAPI app with a fake authenticated user
(set via request.state like the real middleware) and the in-memory test DB."""
import uuid

import httpx
from fastapi import FastAPI, Request

from app.auth.providers.base import AuthUser
from app.core.database import get_db, get_db_streaming

TENANT = uuid.uuid4()


def make_user(role="owner", tenant=TENANT, user_id=None) -> AuthUser:
    return AuthUser(id=str(user_id or uuid.uuid4()), email="u@x.io", name="u", tenant_id=str(tenant),
                    tenant_slug="t", role=role, auth_provider="local")


def make_app(db, user: AuthUser, *routers, prefix="/api/v1") -> FastAPI:
    app = FastAPI()
    for r in routers:
        app.include_router(r, prefix=prefix)

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_db_streaming] = _db
    return app


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
