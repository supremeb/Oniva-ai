"""Backend process for the engine smoke test (test-only; run as ``python -m tests.e2e.backend_app``).

The real ``/internal`` router and the browser-facing ``/api/v1/engine/conversations`` router on a
file SQLite database, served by uvicorn. User auth is replaced by a fixed seeded user (the full
auth stack needs Postgres + Redis, which this smoke does not boot). It also registers one extra
write skill, ``e2e_write``, that blocks on a gate file and then appends to an effects file, so the
test can count real side effects and hold one in flight while the worker is killed.

It also mounts the Deep Work routes (``/api/v1/deep-work``) with the tenant opted in to engine Deep
Work, and swaps the network pieces for the fakes in ``dw_fakes`` (web search, E2B, proxy tokens, Redis).

Config comes from the environment: E2E_PORT, E2E_DB, E2E_INFO (json written when ready),
E2E_EFFECTS, E2E_GATE, E2E_TENANT_ID, E2E_USER_ID, E2E_PROJECT_ID, E2E_DW_DIR, ENGINE_*.
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
from fastapi import FastAPI, Request
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
from app.internal_api.tool_specs import TOOL_SPECS, ToolSpec  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.llm_provider import LLMProvider  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.project_member import ProjectMember  # noqa: E402
from app.models.tenant import Tenant  # noqa: E402
from app.models.user import User  # noqa: E402
from tests.e2e import dw_fakes  # noqa: E402

E = os.environ
PORT, DB, INFO = int(E["E2E_PORT"]), E["E2E_DB"], E["E2E_INFO"]
EFFECTS, GATE = Path(E["E2E_EFFECTS"]), Path(E["E2E_GATE"])
SEED = E.get("E2E_SEED", "1") == "1"
DW = dw_fakes.Files(Path(E["E2E_DW_DIR"]))
TENANT, USER, PROJECT = (uuid.UUID(E[k]) for k in ("E2E_TENANT_ID", "E2E_USER_ID", "E2E_PROJECT_ID"))

engine = create_async_engine(f"sqlite+aiosqlite:///{DB}", connect_args={"timeout": 30})
maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
LLMProvider.__table__.c.models.default = None

# The signed audit chain runs for real here; SQLite has no ARRAY, so the log's array columns are JSON.
from sqlalchemy import JSON  # noqa: E402
from sqlalchemy.dialects.postgresql import ARRAY  # noqa: E402
from app.models.compliance_audit_log import ComplianceAuditLog  # noqa: E402

for _col in ComplianceAuditLog.__table__.c:
    if isinstance(_col.type, ARRAY):
        _col.type = JSON()


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
# A workspace write: the orchestrator reads the web first, and an outside-world write after that would need approval.
TOOL_SPECS["e2e_hold"] = ToolSpec(side_effect="write", idempotent=False, timeout_ms=60_000, scope="workspace")


@asynccontextmanager
async def lifespan(_app):
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    await tools.ensure_skills_loaded()
    dw_fakes.install(DW)  # after the real skills are loaded: web_search, E2B, tokens, Redis
    dw_fakes.install_approval_fakes(DW)  # the sandbox behind execute_python, an offline browse_url
    if not SEED:
        Path(INFO).write_text(json.dumps({"ready": True}))
        yield
        return
    async with maker() as db:
        db.add(Tenant(id=TENANT, name="e2e", slug=f"e2e-{TENANT.hex[:6]}", is_active=True,
                      settings={"features": {"ENABLE_ENGINE_CHAT": True, "ENABLE_ENGINE_DEEP_WORK": True}}))
        await db.commit()
        db.add(User(id=USER, tenant_id=TENANT, email="e2e@x.io", name="e2e"))
        await db.commit()
        db.add(Project(id=PROJECT, tenant_id=TENANT, name="e2e", owner_id=USER))
        await db.commit()
        await _seed_identities(db)
        db.add(LLMProvider(
            tenant_id=TENANT, name="anthropic", display_name="anthropic", provider_type="anthropic",
            api_key_encrypted=get_encryption_service().encrypt("sk-ant-FAKE-e2e-key-0123456789"),
            default_model="anthropic/claude-sonnet-4-5", priority=0, is_enabled=True))
        await db.commit()
    Path(INFO).write_text(json.dumps({"ready": True}))
    yield


# --- identities for the approvals e2e: ``X-E2E-User`` picks one (default: the member who owns the chat) ---
ADMIN, VIEWER, OUTSIDER, STRANGER = (uuid.UUID(E[k]) for k in (
    "E2E_ADMIN_ID", "E2E_VIEWER_ID", "E2E_OUTSIDER_ID", "E2E_STRANGER_ID"))
OTHER_TENANT, OTHER_PROJECT = uuid.UUID(E["E2E_OTHER_TENANT_ID"]), uuid.UUID(E["E2E_OTHER_PROJECT_ID"])
IDENTITIES = {  # name -> (id, tenant, role)
    "member": (USER, TENANT, "member"), "admin": (ADMIN, TENANT, "admin"), "viewer": (VIEWER, TENANT, "viewer"),
    "outsider": (OUTSIDER, TENANT, "member"), "stranger": (STRANGER, OTHER_TENANT, "admin"),
}


async def _seed_identities(db):
    """An admin and a viewer on the project, a member of another project, and an admin of another tenant."""
    db.add(Tenant(id=OTHER_TENANT, name="other", slug=f"other-{OTHER_TENANT.hex[:6]}", is_active=True,
                  settings={"features": {"ENABLE_ENGINE_CHAT": True}}))
    await db.commit()
    for name in ("admin", "viewer", "outsider", "stranger"):
        uid, tenant, role = IDENTITIES[name]
        db.add(User(id=uid, tenant_id=tenant, email=f"{name}@x.io", name=name, role=role))
    await db.commit()
    db.add(Project(id=OTHER_PROJECT, tenant_id=TENANT, name="other project", owner_id=OUTSIDER))
    await db.commit()
    db.add(ProjectMember(project_id=PROJECT, user_id=ADMIN, role="editor", tenant_id=TENANT))
    db.add(ProjectMember(project_id=PROJECT, user_id=VIEWER, role="viewer", tenant_id=TENANT))
    await db.commit()


def _identity(request: Request) -> AuthUser:
    name = request.headers.get("x-e2e-user", "member")
    uid, tenant, role = IDENTITIES[name]
    slug = "e2e" if tenant == TENANT else "other"
    return AuthUser(id=str(uid), email=f"{name}@x.io", name=name, tenant_id=str(tenant), tenant_slug=slug,
                    role=role, auth_provider="local")


settings = Settings(
    _env_file=None, SECRET_KEY=E["E2E_SECRET_KEY"], ENGINE_GATEWAY_TOKEN=E["ENGINE_GATEWAY_TOKEN"],
    ENGINE_SERVICE_TOKEN=E["ENGINE_SERVICE_TOKEN"], ENGINE_URL=E["ENGINE_URL"],
    ENGINE_SESSION_ID=E["ENGINE_SESSION_ID"], ENABLE_ENGINE_API=True,
    ENABLE_ENGINE_CHAT=True, ENABLE_ENGINE_DEEP_WORK=True)
app = FastAPI(lifespan=lifespan)
mount_internal_router(app, settings)
assert mount_engine_conversations(app, settings)

app.dependency_overrides[get_db] = request_db
app.dependency_overrides[get_system_db] = request_db
app.dependency_overrides[get_session_factory] = lambda: factory
app.dependency_overrides[get_current_user] = _identity
app.dependency_overrides[get_current_user_id] = lambda: USER
app.dependency_overrides[get_current_tenant_id] = lambda: TENANT
app.state.engine_deep_work_settings = settings
app.dependency_overrides[get_current_user_with_query_token] = _identity

# --- test-only additions for the browser run (frontend/e2e) ---------------------------------------
import app.api.v1.engine_conversations as _conversations  # noqa: E402

_build_agent = _conversations.build_engine_chat_agent


async def _build_agent_with_e2e_tools(*args, **kwargs):
    """The composer offers no way to pick the e2e tools, so the allow-list always carries them."""
    built = await _build_agent(*args, **kwargs)
    built.agent["tools"] = sorted({*built.agent["tools"], "e2e_write", "kyc_verification", "execute_python",
                                   "browse_url", "research_worker"})
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


# ``requires_approval`` tools open their own session (the execution guard, ``needs_db`` tools): this database.
import app.internal_api.execution as _execution  # noqa: E402

_execution._session_factory = lambda: maker

@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """Leave the traceback in backend.log (the app's logging config otherwise swallows it)."""
    import traceback
    from fastapi.responses import PlainTextResponse

    traceback.print_exception(exc)
    return PlainTextResponse("Internal Server Error", status_code=500)


# Every conversation has an owner today (``engine_conversations.user_id`` is NOT NULL; Fleet sets an explicit
# origin in phase 6), so an unattended run is a seam: conversations listed in E2E_UNATTENDED have no approver.
import app.internal_api.authorize as _authorize  # noqa: E402

_attended = _authorize.is_attended


def _is_attended(conv):
    path = Path(E["E2E_UNATTENDED"])
    listed = path.read_text().split() if path.exists() else []
    return str(conv.conversation_id) not in listed and _attended(conv)


_authorize.is_attended = _is_attended

# --- approvals: the inbox, and the signed audit chain read back from inside this process ---------
from sqlalchemy import select  # noqa: E402

from app.api.v1 import inbox as _inbox  # noqa: E402
from app.services.audit_chain import AuditChain  # noqa: E402

app.include_router(_inbox.router, prefix="/api/v1", tags=["inbox"])


@app.get("/e2e/audit")
async def _audit():
    """The tenant's chain verdict and its entries in order (the real chain, the local-dev KMS)."""
    async with factory() as db:
        verify = await AuditChain(db).verify_chain(TENANT)
        rows = (await db.execute(select(ComplianceAuditLog).where(ComplianceAuditLog.tenant_id == TENANT)
                                 .order_by(ComplianceAuditLog.seq))).scalars().all()
    return {"verify": verify, "entries": [
        {"seq": r.seq, "action": r.action, "decision": r.decision,
         "actor_role": (r.extra_metadata or {}).get("actor_role"), "tool": (r.tools_called or [None])[0]}
        for r in rows]}


@app.get("/api/v1/auth/me")
async def _me():
    return {"id": str(USER), "email": "e2e@x.io", "name": "e2e", "role": "analyst", "tenant_id": str(TENANT),
            "tenant_slug": "e2e", "groups": [], "is_platform_operator": False,
            "features": {"ENABLE_ENGINE_CHAT": True, "ENABLE_ENGINE_DEEP_WORK": True}}


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
