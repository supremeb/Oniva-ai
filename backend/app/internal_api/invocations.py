"""Outcome store and binding checks for the tool gateway (U5 / R3, R6, R8, KTD5/KTD6).

``tool_invocations`` is keyed by the engine's ``Idempotency-Key``. A call is claimed ``in_flight``
with an insert that loses to a concurrent duplicate, the effect runs, and the outcome is stored as
``completed``. A completed key is answered from the store and never re-run.
"""
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.database import get_system_session
from app.internal_api.execution import ToolOutcome
from app.internal_api.fence import GatewayError, SessionFactory
from app.models.engine import EngineConversation, ToolInvocation
from app.models.project import Project
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

# A claim older than the tool's timeout plus this grace belongs to a dead worker (KTD6).
STALE_GRACE_S = 30
NIL_USER = uuid.UUID(int=0)

INTERRUPTED_OUTCOME: Dict[str, Any] = {
    "content": "The call was interrupted before its outcome was recorded. It may have taken effect; check before calling again.",
    "isError": True,
    "details": {"error": "interrupted", "outcome": "unknown"},
}


def get_session_factory() -> SessionFactory:
    """FastAPI dependency: system-role sessions (tests override it)."""
    return get_system_session


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite drops tzinfo


def args_hash(args: Dict[str, Any]) -> str:
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def outcome_to_dict(outcome: ToolOutcome) -> Dict[str, Any]:
    return {"content": outcome.content, "isError": bool(outcome.is_error), "details": outcome.details or {}}


def is_stale(started_at: datetime, timeout_ms: int, now: Optional[datetime] = None) -> bool:
    return (now or utcnow()) - _aware(started_at) > timedelta(milliseconds=timeout_ms, seconds=STALE_GRACE_S)


def _insert(db: Any):
    return (pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert)(ToolInvocation)


# --- binding (R3) -------------------------------------------------------------


def _uuid(value: Optional[str], field: str) -> Optional[uuid.UUID]:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise GatewayError(400, {"error": "invalid_request", "message": f"{field} is not a UUID"})


async def resolve_binding(
    factory: SessionFactory, tenant_id: str, project_id: str, conversation_id: Optional[str]
) -> uuid.UUID:
    """Verify tenant, project and conversation; return the user the tool runs as (from the conversation)."""
    tenant, project, conv = _uuid(tenant_id, "tenantId"), _uuid(project_id, "projectId"), _uuid(conversation_id, "conversationId")
    async with factory() as db:
        t = (await db.execute(select(Tenant.id, Tenant.is_active).where(Tenant.id == tenant))).first()
        if t is None or not t.is_active:
            raise GatewayError(404, {"error": "tenant_not_found"})
        p = (await db.execute(select(Project.id).where(Project.id == project, Project.tenant_id == tenant))).scalar_one_or_none()
        if p is None:
            raise GatewayError(404, {"error": "project_not_found"})
        if conv is None:
            in_use = (
                await db.execute(select(EngineConversation.conversation_id).where(EngineConversation.tenant_id == tenant).limit(1))
            ).scalar_one_or_none()
            if in_use is not None:
                raise GatewayError(400, {"error": "conversation_required"})
            return NIL_USER
        row = (
            await db.execute(
                select(EngineConversation.user_id).where(
                    EngineConversation.conversation_id == conv,
                    EngineConversation.tenant_id == tenant,
                    EngineConversation.project_id == project,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise GatewayError(404, {"error": "conversation_not_found"})
        return row


async def conversation_knowledge_bases(factory: SessionFactory, tenant_id: str, conversation_id: Optional[str]) -> Optional[list]:
    """The KB ids recorded on an engine conversation's selections; ``None`` when it has no record."""
    conv = _uuid(conversation_id, "conversationId")
    if conv is None:
        return None
    async with factory() as db:
        row = (
            await db.execute(
                select(EngineConversation.selections).where(
                    EngineConversation.conversation_id == conv,
                    EngineConversation.tenant_id == _uuid(tenant_id, "tenantId"),
                )
            )
        ).first()
    if row is None or row[0] is None:
        return None  # no recorded selections (conversation predates chat selections): unbound
    return list(row[0].get("knowledge_base_ids") or [])


CONNECTOR_ID_TOOLS = {"invoke_connector_tool", "get_connector_schema"}
CONNECTOR_PROVIDER_TOOLS = {
    "linear_create_issue": "linear", "linear_search_issues": "linear",
    "servicenow_create_incident": "servicenow", "servicenow_search_incidents": "servicenow",
}


async def connector_denial(
    factory: SessionFactory, tenant_id: str, conversation_id: Optional[str], name: str, args: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """A ``connector_not_allowed`` tool outcome when the call targets a connector outside the conversation's selections.

    Applies to ``invoke_connector_tool`` / ``get_connector_schema`` (by ``connector_id``) and to the
    linear / servicenow wrappers (by provider of the selected connectors). A conversation with no
    recorded selections is unbound (predates chat selections), like ``search_knowledge``.
    """
    if name not in CONNECTOR_ID_TOOLS and name not in CONNECTOR_PROVIDER_TOOLS:
        return None
    conv = _uuid(conversation_id, "conversationId")
    if conv is None:
        return None
    tenant = _uuid(tenant_id, "tenantId")
    async with factory() as db:
        row = (await db.execute(
            select(EngineConversation.selections).where(
                EngineConversation.conversation_id == conv, EngineConversation.tenant_id == tenant)
        )).first()
        if row is None or row[0] is None:
            return None
        allowed = [str(c) for c in (row[0].get("connector_ids") or [])]
        if name in CONNECTOR_ID_TOOLS:
            target = str((args or {}).get("connector_id", ""))
            ok = target in allowed
        else:
            ids = [u for u in (_try_uuid(c) for c in allowed) if u is not None]
            providers = set()
            if ids:
                from app.models.connector import Connector
                providers = {p for (p,) in (await db.execute(
                    select(Connector.provider).where(Connector.tenant_id == tenant, Connector.id.in_(ids)))).all()}
            ok = CONNECTOR_PROVIDER_TOOLS[name] in providers
    if ok:
        return None
    return {
        "content": "This connector is not enabled for this conversation.",
        "isError": True,
        "details": {"error": "connector_not_allowed", "tool": name},
    }


def _try_uuid(value: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


# --- claim / store ------------------------------------------------------------


@dataclass
class Begin:
    """``run``: this request owns the effect. ``stored``: answer ``outcome``. ``busy``: a live duplicate."""

    kind: str
    outcome: Optional[Dict[str, Any]] = None


def _reuse() -> GatewayError:
    return GatewayError(422, {"error": "idempotency_key_reuse"})


async def begin(
    factory: SessionFactory,
    *,
    key: str,
    tenant_id: str,
    session_id: str,
    tool: str,
    arguments_hash: str,
    fence_token: Optional[int],
    timeout_ms: int,
    idempotent: bool,
) -> Begin:
    """Claim ``key`` or report what already holds it (see KTD6 for the stale rules)."""
    tenant = uuid.UUID(str(tenant_id))
    for _ in range(3):
        async with factory() as db:
            claimed = await db.execute(
                _insert(db)
                .values(
                    id=uuid.uuid4(), tenant_id=tenant, idempotency_key=key, session_id=session_id, tool=tool,
                    args_hash=arguments_hash, status="in_flight", fence_token=fence_token, started_at=utcnow(),
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
            )
            if claimed.rowcount:
                return Begin("run")
            row = (await db.execute(select(ToolInvocation).where(ToolInvocation.idempotency_key == key))).scalar_one_or_none()
            if row is None:  # released between the insert and the read: claim again
                continue
            # Another tenant's key, a different tool or different arguments: never answer from it.
            if row.tenant_id != tenant or row.tool != tool or row.args_hash != arguments_hash:
                raise _reuse()
            if row.status == "completed":
                return Begin("stored", row.outcome)
            if not is_stale(row.started_at, timeout_ms):
                return Begin("busy")
            # Stale in_flight: compare-and-swap so only one request takes it over.
            if idempotent:
                taken = await db.execute(
                    update(ToolInvocation)
                    .where(
                        ToolInvocation.idempotency_key == key,
                        ToolInvocation.status == "in_flight",
                        ToolInvocation.started_at == row.started_at,
                    )
                    .values(started_at=utcnow(), fence_token=fence_token, session_id=session_id)
                )
                return Begin("run") if taken.rowcount else Begin("busy")
            swept = await db.execute(
                update(ToolInvocation)
                .where(ToolInvocation.idempotency_key == key, ToolInvocation.status == "in_flight")
                .values(status="completed", outcome=INTERRUPTED_OUTCOME, completed_at=utcnow())
            )
            if swept.rowcount:
                return Begin("stored", INTERRUPTED_OUTCOME)
            continue  # completed by its owner meanwhile: read it
    return Begin("busy")


async def store_outcome(factory: SessionFactory, key: str, tenant_id: str, outcome: Dict[str, Any]) -> bool:
    async with factory() as db:
        res = await db.execute(
            update(ToolInvocation)
            .where(
                ToolInvocation.idempotency_key == key,
                ToolInvocation.tenant_id == uuid.UUID(str(tenant_id)),
                ToolInvocation.status == "in_flight",
            )
            .values(status="completed", outcome=outcome, completed_at=utcnow())
        )
        return bool(res.rowcount)


async def release_claim(factory: SessionFactory, key: str, tenant_id: str) -> None:
    """Drop an in-flight claim whose effect never started, so a retry can run."""
    try:
        async with factory() as db:
            await db.execute(
                delete(ToolInvocation).where(
                    ToolInvocation.idempotency_key == key,
                    ToolInvocation.tenant_id == uuid.UUID(str(tenant_id)),
                    ToolInvocation.status == "in_flight",
                )
            )
    except Exception:
        logger.warning("Could not release claim %s", key, exc_info=True)


async def completed_outcome(factory: SessionFactory, key: str, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """The stored outcome for reconcile: completed only, and from the named session when one is given."""
    async with factory() as db:
        row = (await db.execute(select(ToolInvocation).where(ToolInvocation.idempotency_key == key))).scalar_one_or_none()
    if row is None or row.status != "completed" or row.outcome is None:
        return None
    if session_id is not None and row.session_id != session_id:
        return None
    return row.outcome


async def interrupt_stale(factory: SessionFactory, stale_seconds: int, idempotent_tools: set) -> int:
    """Startup sweep: stale in-flight non-idempotent claims get the ``interrupted/unknown`` outcome."""
    cutoff = utcnow() - timedelta(seconds=stale_seconds)
    async with factory() as db:
        res = await db.execute(
            update(ToolInvocation)
            .where(
                ToolInvocation.status == "in_flight",
                ToolInvocation.started_at < cutoff,
                ToolInvocation.tool.not_in(sorted(idempotent_tools)),
            )
            .values(status="completed", outcome=INTERRUPTED_OUTCOME, completed_at=utcnow())
        )
        await db.commit()
        return res.rowcount or 0
