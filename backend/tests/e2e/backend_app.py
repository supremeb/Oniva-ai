"""Backend process for the engine smoke test (test-only; run as ``python -m tests.e2e.backend_app``).

The real ``/internal`` router and the browser-facing ``/api/v1/engine/conversations`` router on a
file SQLite database, served by uvicorn. User auth is replaced by a fixed seeded user (the full
auth stack needs Postgres + Redis, which this smoke does not boot). It also registers one extra
write skill, ``e2e_write``, that blocks on a gate file and then appends to an effects file, so the
test can count real side effects and hold one in flight while the worker is killed.

It also mounts the Deep Work routes (``/api/v1/deep-work``) with the tenant opted in to engine Deep
Work, and swaps the network pieces for the fakes in ``dw_fakes`` (web search, E2B, proxy tokens, Redis).

It mounts the Fleet routes (``/api/v1/background-jobs``, ``/api/v1/fleet``) with the tenant opted in to engine
Fleet, bootstraps the run-handler registry (the ``/internal/runs/finished`` push is already part of the internal
router) and runs the run reconciler. The seeded Fleet agent delivers to ``/e2e/webhook`` on this backend.

Config comes from the environment: E2E_PORT, E2E_DB, E2E_INFO (json written when ready),
E2E_EFFECTS, E2E_GATE, E2E_TENANT_ID, E2E_USER_ID, E2E_PROJECT_ID, E2E_AGENT_ID, E2E_DW_DIR, ENGINE_*.
``E2E_SEED=0`` starts a second backend process on the same database without seeding it.
"""
import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import tests.conftest  # noqa: F401  (registers the SQLite type compilers)

import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.database as dbmod

dbmod.set_tenant_context = AsyncMock()  # SQLite has no RLS settings

from app.agents.skills.registry import Skill, _registry  # noqa: E402
from app.api.v1.engine_conversations import mount_engine_conversations  # noqa: E402
from app.auth.dependencies import (  # noqa: E402
    get_current_tenant_id, get_current_user, get_current_user_id, get_current_user_with_query_token)
from app.auth.providers.base import AuthUser  # noqa: E402
from app.config import Settings  # noqa: E402
from app.core.database import get_db, get_system_db  # noqa: E402
from app.core.encryption import get_encryption_service  # noqa: E402
from app.internal_api import tools  # noqa: E402
from app.internal_api.invocations import get_session_factory  # noqa: E402
from app.internal_api.router import mount_internal_router  # noqa: E402
from app.services import run_handlers  # noqa: E402
from app.services.engine_client import get_engine_client  # noqa: E402
from app.services.run_reconciler import RunReconciler  # noqa: E402
from app.internal_api.tool_specs import TOOL_SPECS, ToolSpec  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.custom_agent import CustomAgent  # noqa: E402
from app.models.llm_provider import LLMProvider  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.tenant import Tenant  # noqa: E402
from app.models.user import User  # noqa: E402
from tests.e2e import dw_fakes  # noqa: E402

E = os.environ
PORT, DB, INFO = int(E["E2E_PORT"]), E["E2E_DB"], E["E2E_INFO"]
EFFECTS, GATE = Path(E["E2E_EFFECTS"]), Path(E["E2E_GATE"])
SEED = E.get("E2E_SEED", "1") == "1"
DW = dw_fakes.Files(Path(E["E2E_DW_DIR"]))
TENANT, USER, PROJECT, AGENT = (uuid.UUID(E[k]) for k in (
    "E2E_TENANT_ID", "E2E_USER_ID", "E2E_PROJECT_ID", "E2E_AGENT_ID"))

engine = create_async_engine(f"sqlite+aiosqlite:///{DB}", connect_args={"timeout": 30})
maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
LLMProvider.__table__.c.models.default = None


@asynccontextmanager
async def factory():
    async with maker() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


async def request_db():
    async with factory() as s:
        yield s


async def e2e_write(ctx, note: str = "x"):
    """Append one line to the effects file once the gate file exists (a blocked write)."""
    while not GATE.exists():
        await asyncio.sleep(0.05)
    with EFFECTS.open("a") as f:
        f.write(f"effect {note}\n")
    return {"written": note}


_registry["e2e_write"] = Skill(name="e2e_write", description="e2e write", category="test", handler=e2e_write)
TOOL_SPECS["e2e_write"] = ToolSpec(side_effect="write", idempotent=False, timeout_ms=60_000)
# The same gated write under another name, for Deep Work workers (the chat script owns e2e_write's results).
_registry["e2e_hold"] = Skill(name="e2e_hold", description="e2e held write", category="test", handler=e2e_write)
TOOL_SPECS["e2e_hold"] = ToolSpec(side_effect="write", idempotent=False, timeout_ms=60_000)


# The Fleet browser scenarios' held write (its own name: the Deep Work script has rules on e2e_hold results).
_registry["e2e_fleet_hold"] = Skill(name="e2e_fleet_hold", description="e2e fleet held write", category="test",
                                    handler=e2e_write)
TOOL_SPECS["e2e_fleet_hold"] = ToolSpec(side_effect="write", idempotent=False, timeout_ms=60_000)


@asynccontextmanager
async def lifespan(_app):
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    await tools.ensure_skills_loaded()
    dw_fakes.install(DW)  # after the real skills are loaded: web_search, E2B, tokens, Redis
    run_handlers.bootstrap()  # the fleet start / settle hooks, as main.py does
    _serialize_fleet_settle()
    reconciler = RunReconciler(factory, settings, engine_factory=get_engine_client)
    reconciler.start()
    try:
        await _seed()
        yield
    finally:
        await reconciler.stop()


def _serialize_fleet_settle() -> None:
    """One settle at a time: SQLite ignores ``SELECT ... FOR UPDATE``, which Postgres uses to make the second of
    two near-simultaneous pushes (a steered run settles two submissions) see the row already terminal."""
    inner, lock = run_handlers.hook("fleet", "on_finished"), asyncio.Lock()

    async def serialized(ref, snapshot):
        async with lock:
            await inner(ref, snapshot)

    run_handlers._registry["fleet"]["on_finished"] = serialized


async def _seed():
    if not SEED:
        Path(INFO).write_text(json.dumps({"ready": True}))
        return
    async with maker() as db:
        db.add(Tenant(id=TENANT, name="e2e", slug=f"e2e-{TENANT.hex[:6]}", is_active=True,
                      settings={"features": {"ENABLE_ENGINE_CHAT": True, "ENABLE_ENGINE_DEEP_WORK": True,
                                             "ENABLE_ENGINE_FLEET": True}}))
        await db.commit()
        db.add(User(id=USER, tenant_id=TENANT, email="e2e@x.io", name="e2e"))
        await db.commit()
        db.add(Project(id=PROJECT, tenant_id=TENANT, name="e2e", owner_id=USER))
        db.add(LLMProvider(
            tenant_id=TENANT, name="anthropic", display_name="anthropic", provider_type="anthropic",
            api_key_encrypted=get_encryption_service().encrypt("sk-ant-FAKE-e2e-key-0123456789"),
            default_model="anthropic/claude-sonnet-4-5", priority=0, is_enabled=True))
        await db.commit()
        # Core insert: explicit NULLs for the ARRAY columns SQLite cannot bind (tools are set in the seam below).
        await db.execute(CustomAgent.__table__.insert().values(
            id=AGENT, user_id=USER, tenant_id=TENANT, name="fleet-e2e", system_prompt="Check the vendors.",
            model="anthropic/claude-sonnet-4-5", skills=None, connector_ids=None, knowledge_base_ids=None,
            workflow_ids=None, compliance_tags=None,
            resource_constraints={"max_tool_calls": 12, "timeout_seconds": 600},
            background_config={"webhook_url": f"http://127.0.0.1:{PORT}/e2e/webhook"}))
        await db.commit()
    Path(INFO).write_text(json.dumps({"ready": True}))


settings = Settings(
    _env_file=None, SECRET_KEY=E["E2E_SECRET_KEY"], ENGINE_GATEWAY_TOKEN=E["ENGINE_GATEWAY_TOKEN"],
    ENGINE_SERVICE_TOKEN=E["ENGINE_SERVICE_TOKEN"], ENGINE_URL=E["ENGINE_URL"],
    ENGINE_SESSION_ID=E["ENGINE_SESSION_ID"], ENABLE_ENGINE_API=True,
    ENABLE_ENGINE_CHAT=True, ENABLE_ENGINE_DEEP_WORK=True, ENABLE_ENGINE_FLEET=True,
    RUN_RECONCILE_INTERVAL_S=3, RUN_RECONCILE_AFTER_S=10)
app = FastAPI(lifespan=lifespan)
mount_internal_router(app, settings)
assert mount_engine_conversations(app, settings)

_user = AuthUser(id=str(USER), email="e2e@x.io", tenant_id=str(TENANT), tenant_slug="e2e",
                 role="analyst", auth_provider="local")
app.dependency_overrides[get_db] = request_db
app.dependency_overrides[get_system_db] = request_db
app.dependency_overrides[get_session_factory] = lambda: factory
app.dependency_overrides[get_current_user] = lambda: _user
app.dependency_overrides[get_current_user_id] = lambda: USER
app.dependency_overrides[get_current_tenant_id] = lambda: TENANT
app.state.engine_deep_work_settings = settings
app.dependency_overrides[get_current_user_with_query_token] = lambda: _user

# --- test-only additions for the browser run (frontend/e2e) ---------------------------------------
import app.api.v1.engine_conversations as _conversations  # noqa: E402

_build_agent = _conversations.build_engine_chat_agent


async def _build_agent_with_e2e_tools(*args, **kwargs):
    """The composer offers no way to pick the e2e tools, so the allow-list always carries them."""
    built = await _build_agent(*args, **kwargs)
    built.agent["tools"] = sorted({*built.agent["tools"], "e2e_write", "kyc_verification"})
    return built


_conversations.build_engine_chat_agent = _build_agent_with_e2e_tools

if E.get("E2E_CORS_ORIGIN"):  # the Next.js app calls the API cross-origin with cookies
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(CORSMiddleware, allow_origins=[E["E2E_CORS_ORIGIN"]], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])


# --- Deep Work: the real routes and services, on this process's SQLite database --------------------
import app.services.deep_work.engine_tools as _dw_tools  # noqa: E402
from app.api.v1 import deep_work as _deep_work  # noqa: E402
from app.api.v1 import deep_work_engine as _deep_work_engine  # noqa: E402

_tenant_lock = asyncio.Lock()


@asynccontextmanager
async def tenant_session(_tenant_id):
    """One tenant session at a time: SQLite ignores ``SELECT ... FOR UPDATE``, which Postgres uses to
    serialize two workers saving into the same run at once (a lost update here otherwise)."""
    async with _tenant_lock:
        async with factory() as s:
            yield s


_deep_work_engine.get_db_for_tenant = tenant_session
_dw_tools.get_db_for_tenant = tenant_session
app.include_router(_deep_work.router, prefix="/api/v1", tags=["deep-work"])

import app.services.engine_deep_work_agent as _dw_agent  # noqa: E402

_compose_execute_config = _dw_agent.compose_execute_config


def _compose_with_e2e_hold(inputs, now=None):
    """Test seam (like the chat one): the orchestrator may call ``e2e_hold``, a gated non-replayable write."""
    config = _compose_execute_config(inputs, now)
    config["tools"] = sorted({*config["tools"], "e2e_hold"})
    return config


_dw_agent.compose_execute_config = _compose_with_e2e_hold


# --- Fleet: the real routes, hooks and engine start on this process's SQLite database ---------------
import app.services.fleet_engine as _fleet_engine  # noqa: E402
from app.api.v1 import background_jobs as _background_jobs  # noqa: E402
from app.api.v1 import fleet as _fleet  # noqa: E402

# Each Fleet session is short and sequential; no process-wide lock (a nested session would deadlock on it).
_fleet_engine.get_db_for_tenant = lambda _tenant_id: factory()

_build_fleet_config = _fleet_engine.build_fleet_agent_config


def _fleet_config_with_e2e_tools(*args, **kwargs):
    """Test seam (like the chat one): CustomAgent.skills is an ARRAY SQLite cannot hold, so the tools are set here."""
    built = _build_fleet_config(*args, **kwargs)
    built.config["tools"] = ["web_search", "e2e_hold", "e2e_fleet_hold"]
    return built


_fleet_engine.build_fleet_agent_config = _fleet_config_with_e2e_tools
app.include_router(_background_jobs.router, prefix="/api/v1/background-jobs", tags=["background-jobs"])
app.include_router(_fleet.router, prefix="/api/v1", tags=["fleet"])


@app.post("/e2e/webhook")
async def _webhook(payload: dict):
    """The agent's delivery target: one line per delivered result."""
    DW.append(DW.webhook_log, json.dumps(payload))
    return {"ok": True}


@app.get("/api/v1/auth/me")
async def _me():
    return {"id": str(USER), "email": "e2e@x.io", "name": "e2e", "role": "analyst", "tenant_id": str(TENANT),
            "tenant_slug": "e2e", "groups": [], "is_platform_operator": False,
            "features": {"ENABLE_ENGINE_CHAT": True, "ENABLE_ENGINE_DEEP_WORK": True,
                         "ENABLE_ENGINE_FLEET": True, "ENABLE_FLEET_DASHBOARD": True}}


@app.post("/api/v1/auth/refresh")
async def _refresh():
    return {"user": await _me()}


@app.get("/api/v1/projects")
async def _projects():
    now = "2026-01-01T00:00:00Z"
    proj = {"id": str(PROJECT), "tenant_id": str(TENANT), "owner_id": str(USER), "name": "e2e",
            "description": None, "instructions": None, "default_model": None, "knowledge_base_id": None,
            "settings": {}, "is_archived": False, "row_version": 1, "member_count": 1, "file_count": 0,
            "session_count": 0, "user_role": "owner", "created_at": now, "updated_at": now}
    return {"projects": [proj], "total": 1, "page": 1, "page_size": 100}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
