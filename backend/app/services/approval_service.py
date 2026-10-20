"""The approval ledger service (Approvals & Policy plan, U2 / KTD3, KTD4, KTD5, KTD11).

The engine item stays the source of truth for whether a call runs. This service indexes it
(``record_pending``), decides who may decide (``can_decide``), runs the two-system decide protocol
(``decide``), and closes rows that cannot be decided any more (``expire_overdue``, ``mark_closed``,
``finalize_from_engine``). Every state change that matters is written to the audit chain.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.harness import _scan_credentials
from app.auth.permissions import has_permission
from app.auth.providers.base import AuthUser
from app.core.database import set_tenant_context
from app.models.compliance_audit_log import AuditAction
from app.models.engine import EngineConversation
from app.models.tenant import Tenant
from app.models.tool_approval import OPEN_STATUSES, ToolApproval
from app.models.user import User
from app.security.kms import KeyManagementService
from app.services.audit_chain import AuditChain
from app.services.engine_client import EngineClient, EngineError

logger = logging.getLogger(__name__)

AUDIT_AGENT_ID = "approvals"
# Same pattern as frontend/lib/engine/approval.ts (SENSITIVE_KEY): such keys never reach the ledger.
SENSITIVE_KEY = re.compile(
    r"pass(word|wd)?|secret|token|api[-_]?key|authorization|credential|cookie|private[-_]?key|bearer|session",
    re.I,
)
RECONCILE_MIN_INTERVAL = timedelta(seconds=30)  # per tenant, in-process (KTD11)
RECONCILE_MAX_CONVERSATIONS = 20  # engine conversations read per pass
ORPHAN_GRACE = timedelta(seconds=120)  # authorize-then-engine-commit gap
# A `deciding` row this old (engine 502/timeout left it there) may be reclaimed by any authorised
# decider and is settled against the engine item by reconcile.
DECIDING_STALE = timedelta(seconds=120)
_last_reconcile: dict[str, datetime] = {}


def reset_reconcile_throttle() -> None:
    _last_reconcile.clear()


# --- decision hooks (plan 003 U21) ---------------------------------------------
# A ledger row whose item is not an engine tool approval (a workflow node parked on a human) is decided
# through the same route and RBAC, but delivered by a registered hook instead of ``EngineClient.decide``.
# Keyed by ``(row.kind, conversation.run_kind)``. The hook signature is
# ``async def hook(*, engine, conv, row, user, decision, note) -> dict`` and raises ``EngineError`` when
# the engine cannot take the delivery.
DecisionHook = Callable[..., Awaitable[dict]]
_DECISION_HOOKS: dict[tuple[str, str], DecisionHook] = {}


def register_decision_hook(kind: str, run_kind: str, fn: DecisionHook) -> None:
    _DECISION_HOOKS[(kind, run_kind)] = fn


def get_decision_hook(kind: str, run_kind: Optional[str]) -> Optional[DecisionHook]:
    if kind == "review":  # the workflow module registers itself on import; import lazily (it imports us)
        from app.services import workflow_wait_decisions  # noqa: F401
    return _DECISION_HOOKS.get((kind, run_kind or ""))


# Engine rejection reasons that mean "this call is not decidable here" rather than "unknown".
_NON_DEFINITIVE_REJECTIONS = {"not_owned", "invalid"}


class ApprovalError(Exception):
    """A ledger-level refusal the route layer maps to an HTTP status."""

    def __init__(self, status: int, code: str):
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code


def scrub_arguments(value: Any, tool_name: str = "approval") -> Any:
    """Drop sensitive keys and redact credential-looking strings, recursively."""
    if isinstance(value, dict):
        return {k: scrub_arguments(v, tool_name) for k, v in value.items() if not SENSITIVE_KEY.search(str(k))}
    if isinstance(value, list):
        return [scrub_arguments(v, tool_name) for v in value]
    if isinstance(value, str):
        return _scan_credentials(value, tool_name, None)[0]
    return value


def _uuid(value: Any) -> Optional[UUID]:
    if value is None or isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError):
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)  # SQLite drops tzinfo
    return dt


class ApprovalService:
    def __init__(self, db: AsyncSession, kms: Optional[KeyManagementService] = None,
                 audit: Optional[AuditChain] = None):
        self.db = db
        self.audit = audit or AuditChain(db, kms=kms)

    # --- transactions ---------------------------------------------------------

    async def _commit(self, tenant_id) -> None:
        """Commit, then restore the transaction-local tenant context RLS depends on."""
        await self.db.commit()
        if self.db.get_bind().dialect.name == "postgresql":
            await set_tenant_context(self.db, str(tenant_id))

    # --- reads ----------------------------------------------------------------

    async def get_by_task(self, tenant_id, conversation_id, tool_task_id: str) -> Optional[ToolApproval]:
        res = await self.db.execute(select(ToolApproval).where(
            ToolApproval.tenant_id == _uuid(tenant_id),
            ToolApproval.conversation_id == _uuid(conversation_id),
            ToolApproval.tool_task_id == tool_task_id))
        return res.scalars().first()

    async def get_by_call(self, tenant_id, run_id: str, tool_call_id: str) -> Optional[ToolApproval]:
        res = await self.db.execute(select(ToolApproval).where(
            ToolApproval.tenant_id == _uuid(tenant_id),
            ToolApproval.run_id == run_id, ToolApproval.tool_call_id == tool_call_id))
        return res.scalars().first()

    # --- execution guard (U6 / KTD7) -------------------------------------------

    async def find_for_execution(self, tenant_id, run_id: str, tool_call_id: str,
                                 *, lock: bool = True) -> Optional[ToolApproval]:
        """The ledger row for one tool call, locked ``FOR UPDATE`` so concurrent invokes serialise."""
        stmt = select(ToolApproval).where(
            ToolApproval.tenant_id == _uuid(tenant_id),
            ToolApproval.run_id == run_id, ToolApproval.tool_call_id == tool_call_id)
        if lock:
            stmt = stmt.with_for_update()
        res = await self.db.execute(stmt.execution_options(populate_existing=True))
        return res.scalars().first()

    @staticmethod
    def check_executable(row: Optional[ToolApproval], arguments_hash: str) -> None:
        """Raise ``ApprovalError`` (403) unless ``row`` is an unused approve decision for these arguments."""
        if row is None or row.status != "approved" or row.decision != "approve":
            raise ApprovalError(403, "approval_required")
        if row.consumed_at is not None:
            raise ApprovalError(403, "approval_consumed")
        if not row.arguments_hash or row.arguments_hash != arguments_hash:
            raise ApprovalError(403, "approval_arguments_mismatch")

    async def consume(self, row: ToolApproval) -> None:
        """Mark the approval used. The caller holds the row lock and commits."""
        row.consumed_at = _now()
        row.updated_at = _now()
        await self.db.flush()

    # --- record ---------------------------------------------------------------

    async def record_pending(
        self, *, tenant_id, conversation_id, tool_task_id: Optional[str] = None,
        engine_conversation_id: Optional[str] = None, run_id: Optional[str] = None,
        tool_call_id: Optional[str] = None, tool_name: Optional[str] = None,
        arguments: Optional[dict] = None, arguments_hash: Optional[str] = None,
        reason: Optional[str] = None, rule: str = "requires_approval", approver_role: str = "member",
        requested_by=None, expires_at: Optional[datetime] = None, project_id=None,
        taint_sources: Optional[list] = None, kind: str = "tool_call",
    ) -> ToolApproval:
        """Idempotent upsert: a repeat of the root/task key or the run/call key returns the row."""
        tenant = _uuid(tenant_id)
        existing = await self._find_existing(tenant, conversation_id, tool_task_id, run_id, tool_call_id)
        if existing is not None:
            return existing
        row = ToolApproval(
            id=uuid.uuid4(), tenant_id=tenant, kind=kind, status="pending", project_id=_uuid(project_id),
            conversation_id=_uuid(conversation_id), engine_conversation_id=engine_conversation_id,
            run_id=run_id, tool_call_id=tool_call_id, tool_task_id=tool_task_id, tool_name=tool_name,
            arguments_preview=scrub_arguments(arguments or {}, tool_name or "approval"),
            arguments_hash=arguments_hash, reason=reason, rule=rule, approver_role=approver_role,
            requested_by=_uuid(requested_by), expires_at=expires_at, taint_sources=taint_sources,
            self_approved=False)
        try:
            async with self.db.begin_nested():
                self.db.add(row)
                await self.db.flush()
        except IntegrityError:
            existing = await self._find_existing(tenant, conversation_id, tool_task_id, run_id, tool_call_id)
            if existing is None:
                raise
            return existing
        return row

    async def _find_existing(self, tenant, conversation_id, tool_task_id, run_id, tool_call_id):
        if tool_task_id is not None:
            row = await self.get_by_task(tenant, conversation_id, tool_task_id)
            if row is not None:
                return row
        if run_id is not None and tool_call_id is not None:
            return await self.get_by_call(tenant, run_id, tool_call_id)
        return None

    # --- who may decide (KTD4) ------------------------------------------------

    @staticmethod
    def role_allows(user: AuthUser, row: ToolApproval) -> bool:
        """Pure RBAC half of KTD4 (tenant match is checked by ``can_decide``)."""
        if has_permission(user.role, "approvals:decide"):
            return True
        return (
            has_permission(user.role, "approvals:decide_own")
            and row.approver_role == "member"
            and row.requested_by is not None
            and str(row.requested_by) == str(user.id)
        )

    async def _admin_or_owner_count(self, tenant_id) -> int:
        res = await self.db.execute(select(func.count()).select_from(User).where(
            User.tenant_id == _uuid(tenant_id), User.role.in_(("admin", "owner")),
            User.is_active.is_(True)))
        return int(res.scalar() or 0)

    async def _require_separate_approver(self, tenant_id) -> bool:
        tenant = await self.db.get(Tenant, _uuid(tenant_id))
        cfg = ((tenant.settings or {}).get("approvals") or {}) if tenant is not None else {}
        return cfg.get("require_separate_approver") is True

    async def can_decide(self, user: AuthUser, row: ToolApproval) -> bool:
        """Return whether the decision would be a self-approval; raise ``ApprovalError`` if refused.

        404 other tenant, 403 ``not_authorized``, 403 ``self_approval_forbidden``. Writes nothing.
        """
        if str(row.tenant_id) != str(user.tenant_id):
            raise ApprovalError(404, "not_found")
        if not self.role_allows(user, row):
            raise ApprovalError(403, "not_authorized")
        is_self = row.requested_by is not None and str(row.requested_by) == str(user.id)
        if not is_self:
            return False
        if await self._require_separate_approver(row.tenant_id):
            raise ApprovalError(403, "self_approval_forbidden")
        if row.approver_role == "admin" and await self._admin_or_owner_count(row.tenant_id) >= 2:
            raise ApprovalError(403, "self_approval_forbidden")
        return True

    # --- decide (KTD5) --------------------------------------------------------

    async def decide(self, *, user: AuthUser, conv: EngineConversation, row: ToolApproval,
                     decision: str, note: Optional[str], engine: EngineClient) -> dict:
        """Decide a ledger row through the engine. Returns the engine's response body."""
        hook = get_decision_hook(row.kind, getattr(conv, "run_kind", None))
        if hook is not None:
            if str(row.tenant_id) != str(user.tenant_id):
                raise ApprovalError(404, "not_found")
            return await self._decide_hooked(hook, user=user, conv=conv, row=row, decision=decision,
                                             note=note, engine=engine)
        if row.kind != "tool_call" or str(row.tenant_id) != str(user.tenant_id):
            raise ApprovalError(404, "not_found")
        uid = _uuid(user.id)

        if row.status == "deciding":
            if row.decided_by != uid:
                done = await self._reclaim_stale(row, user, uid, decision, note, engine, conv)
                if done is not None:
                    return done
            else:
                await self.can_decide(user, row)
                decision, note = row.decision or decision, row.note  # a retry repeats the same inputs
        elif row.status == "pending":
            self_approved = await self.can_decide(user, row)
            if not await self._claim(row, uid, decision, note, self_approved):
                await self.db.refresh(row)
                raise ApprovalError(409, "decision_in_progress" if row.status == "deciding" else "already_decided")
        elif row.status in ("approved", "denied") and row.decided_by == uid and row.decision == decision:
            await self.can_decide(user, row)  # idempotent re-send by the decider: ask the engine
        elif row.status == "expired":
            raise ApprovalError(410, "approval_expired")
        elif row.status == "cancelled":
            raise ApprovalError(409, "not_waiting")
        else:
            raise ApprovalError(409, "already_decided")

        try:
            result = await engine.decide(
                conv.session_id, conv.tenant_id, conv.project_id, conv.conversation_id,
                row.tool_task_id, decision, note, principal=str(user.id))
        except EngineError as e:
            done = await self._on_engine_error(row, user, decision, e, engine, conv)
            if done is not None:
                return done
            raise
        await self._finalize_decided(row, decided_by=user, decision=decision, note=note,
                                     engine_item=result.get("approval") if isinstance(result, dict) else None)
        return result

    async def _decide_hooked(self, hook: DecisionHook, *, user: AuthUser, conv: EngineConversation,
                             row: ToolApproval, decision: str, note: Optional[str],
                             engine: EngineClient) -> dict:
        """The KTD5 protocol with a hook as the delivery step. The ledger claim (``pending -> deciding``)
        is the arbiter: only its winner delivers, so the first decision wins and a second is 409."""
        uid = _uuid(user.id)
        retry = False
        if row.status == "pending":
            expires = _aware(row.expires_at)
            if expires is not None and expires <= _now():
                await self._close(row, "expired", AuditAction.APPROVAL_EXPIRED,
                                  "approval not decided before its expiry")
                raise ApprovalError(410, "approval_expired")
            self_approved = await self.can_decide(user, row)
            if not await self._claim(row, uid, decision, note, self_approved):
                await self.db.refresh(row)
                raise ApprovalError(409, "decision_in_progress" if row.status == "deciding" else "already_decided")
        elif row.status == "deciding":
            retry = True
            if row.decided_by != uid:
                seen = _aware(row.updated_at) or _now()
                if _now() - seen < DECIDING_STALE:
                    raise ApprovalError(409, "decision_in_progress")
                self_approved = await self.can_decide(user, row)  # a stuck delivery must not lock everyone out
                res = await self.db.execute(
                    update(ToolApproval)
                    .where(ToolApproval.id == row.id, ToolApproval.status == "deciding",
                           ToolApproval.updated_at == row.updated_at)
                    .values(decided_by=uid, decision=decision, note=note, self_approved=self_approved,
                            updated_at=_now())
                    .execution_options(synchronize_session=False))
                won = res.rowcount == 1
                await self._commit(row.tenant_id)
                await self.db.refresh(row)
                if not won:
                    raise ApprovalError(409, "decision_in_progress")
            else:
                await self.can_decide(user, row)
                decision, note = row.decision or decision, row.note  # a retry repeats the same inputs
        elif row.status in ("approved", "denied") and row.decided_by == uid and row.decision == decision:
            retry = True
            await self.can_decide(user, row)  # idempotent re-send by the decider
        elif row.status == "expired":
            raise ApprovalError(410, "approval_expired")
        elif row.status == "cancelled":
            raise ApprovalError(409, "not_waiting")
        else:
            raise ApprovalError(409, "already_decided")

        try:
            result = await hook(engine=engine, conv=conv, row=row, user=user, decision=decision, note=note)
        except EngineError as e:
            if e.status in (404, 409, 410) and not (retry and e.status == 409):
                # The node is no longer waiting (woke on its SLA, finished, or the run is gone).
                expires = _aware(row.expires_at)
                if expires is not None and expires <= _now():
                    await self._close(row, "expired", AuditAction.APPROVAL_EXPIRED, "workflow wait woke on its SLA")
                    raise ApprovalError(410, "approval_expired") from None
                await self._close(row, "cancelled", AuditAction.APPROVAL_CANCELLED,
                                  "workflow node no longer waiting")
                raise ApprovalError(409, "not_waiting") from None
            if not (retry and e.status == 409):
                raise  # 5xx/timeout: the row stays `deciding`; the decider may retry
            result = {}  # a retry whose first signal already landed: the delivery is done
        await self._finalize_decided(row, decided_by=user, decision=decision, note=note, engine_item=None)
        return result

    async def _reclaim_stale(self, row: ToolApproval, user: AuthUser, uid, decision: str,
                             note: Optional[str], engine: EngineClient, conv: EngineConversation) -> Optional[dict]:
        """Another decider's `deciding` row. Fresh: 409. Older than ``DECIDING_STALE``: a stuck engine
        call must not lock everyone else out, so an authorised decider may take it over, after
        checking the engine item (already decided: settle from it; the caller's call, if identical,
        is a 200). Returns a response body when the engine had decided, ``None`` once reclaimed."""
        seen = _aware(row.updated_at) or _now()
        if _now() - seen < DECIDING_STALE:
            raise ApprovalError(409, "decision_in_progress")
        self_approved = await self.can_decide(user, row)  # 403/404 for anyone not allowed to decide
        items = await engine.approvals(conv.session_id, conv.tenant_id, conv.project_id, conv.conversation_id)
        item = next((i for i in items if i.get("toolTaskId") == row.tool_task_id), None)
        if item is not None and item.get("status") in ("approved", "denied", "expired", "cancelled"):
            await self.finalize_from_engine(row, item)
            await self._commit(row.tenant_id)
            status = item.get("status")
            if status == "expired":
                raise ApprovalError(410, "approval_expired")
            if status == "cancelled":
                raise ApprovalError(409, "not_waiting")
            dec = item.get("decision") or {}
            if str(dec.get("principal")) == str(user.id) and dec.get("decision") == decision:
                return {"approval": item}
            raise ApprovalError(409, "already_decided")
        res = await self.db.execute(
            update(ToolApproval)
            .where(ToolApproval.id == row.id, ToolApproval.status == "deciding",
                   ToolApproval.updated_at == row.updated_at)
            .values(decided_by=uid, decision=decision, note=note, self_approved=self_approved,
                    updated_at=_now())
            .execution_options(synchronize_session=False))
        won = res.rowcount == 1
        await self._commit(row.tenant_id)
        await self.db.refresh(row)
        if not won:
            raise ApprovalError(409, "decision_in_progress")
        return None

    async def _claim(self, row: ToolApproval, uid, decision: str, note: Optional[str],
                     self_approved: bool) -> bool:
        res = await self.db.execute(
            update(ToolApproval)
            .where(ToolApproval.id == row.id, ToolApproval.status == "pending")
            .values(status="deciding", decided_by=uid, decision=decision, note=note,
                    self_approved=self_approved, updated_at=_now())
            .execution_options(synchronize_session=False))
        claimed = res.rowcount == 1
        await self._commit(row.tenant_id)
        if claimed:
            await self.db.refresh(row)
        return claimed

    async def _on_engine_error(self, row, user, decision, e: EngineError, engine, conv) -> Optional[dict]:
        """A definitive rejection settles the row (and raises, or returns the 200 body when the
        engine already recorded this caller's identical decision); anything else leaves it ``deciding``."""
        if e.error != "approval_rejected":
            return None
        reason = e.body.get("reason")
        if reason in _NON_DEFINITIVE_REJECTIONS:
            return None
        item = e.body.get("approval") if isinstance(e.body.get("approval"), dict) else None
        if item is None and reason == "already_decided":  # an engine without the U4 snapshot
            try:
                items = await engine.approvals(conv.session_id, conv.tenant_id, conv.project_id,
                                               conv.conversation_id)
            except EngineError:
                return None
            item = next((i for i in items if i.get("toolTaskId") == row.tool_task_id), None)
        if reason == "expired":
            await self._close(row, "expired", AuditAction.APPROVAL_EXPIRED, "engine rejected: expired")
            raise ApprovalError(410, "approval_expired")
        if reason == "not_waiting":
            await self._close(row, "cancelled", AuditAction.APPROVAL_CANCELLED, "engine rejected: not waiting")
            raise ApprovalError(409, "not_waiting")
        if reason == "already_decided" and item is not None:
            await self.finalize_from_engine(row, item)
            await self._commit(row.tenant_id)
            status = item.get("status")
            if status == "expired":
                raise ApprovalError(410, "approval_expired")
            if status == "cancelled":
                raise ApprovalError(409, "not_waiting")
            dec = item.get("decision") or {}
            if str(dec.get("principal")) == str(user.id) and dec.get("decision") == decision:
                return {"approval": item}
            raise ApprovalError(409, "already_decided")
        return None

    # --- answer (U11) ---------------------------------------------------------

    @staticmethod
    def can_answer(user: AuthUser, conv: EngineConversation) -> bool:
        """The conversation's owner, or a tenant admin or owner. Looser than approvals on purpose:
        answering a question grants no power."""
        return str(conv.user_id) == str(user.id) or user.role in ("admin", "owner")

    async def answer(self, *, user: AuthUser, conv: EngineConversation, row: ToolApproval,
                     answer: dict, engine: EngineClient) -> dict:
        """Answer an ``ask_user`` question through the engine (KTD5 protocol, ``pending -> deciding
        -> answered``). Returns the engine's response body."""
        if row.kind != "question" or str(row.tenant_id) != str(user.tenant_id):
            raise ApprovalError(404, "not_found")
        if not self.can_answer(user, conv):
            raise ApprovalError(403, "not_authorized")
        uid = _uuid(user.id)

        if row.status == "deciding":
            if row.decided_by != uid:
                raise ApprovalError(409, "decision_in_progress")
            try:  # a retry repeats the same inputs
                answer = json.loads(row.note) if row.note else answer
            except ValueError:
                pass
        elif row.status == "pending":
            if not await self._claim(row, uid, "answer", json.dumps(answer, sort_keys=True), False):
                await self.db.refresh(row)
                raise ApprovalError(409, "decision_in_progress" if row.status == "deciding" else "already_answered")
        elif row.status == "answered" and row.decided_by == uid:
            pass  # idempotent re-send by the answerer: the engine says whether it took
        elif row.status == "expired":
            raise ApprovalError(410, "approval_expired")
        elif row.status == "cancelled":
            raise ApprovalError(409, "not_waiting")
        else:
            raise ApprovalError(409, "already_answered")

        try:
            result = await engine.answer(conv.session_id, conv.tenant_id, conv.project_id,
                                         conv.conversation_id, row.tool_task_id, answer, principal=str(user.id))
        except EngineError as e:
            done = await self._on_answer_error(row, user, e)
            if done is not None:
                return done
            raise
        await self._finalize_answered(row, user, answer)
        return result

    async def _finalize_answered(self, row: ToolApproval, user: AuthUser, answer: dict) -> None:
        if row.status not in OPEN_STATUSES:
            return
        row.status, row.decision = "answered", "answer"
        row.decided_by = _uuid(user.id)
        row.decided_at = _now()
        row.updated_at = _now()
        actor = await self._actor(user)
        await self._audit(row, AuditAction.QUESTION_ANSWERED, "answer", actor,
                          rationale="question answered", extra=self._answer_meta(answer))
        await self._commit(row.tenant_id)

    async def _on_answer_error(self, row: ToolApproval, user: AuthUser, e: EngineError) -> Optional[dict]:
        if e.error != "question_rejected":
            return None  # 502/504/timeout: the engine may have taken it, the row stays deciding
        reason = e.body.get("reason")
        item = e.body.get("question") if isinstance(e.body.get("question"), dict) else None
        if reason in _NON_DEFINITIVE_REJECTIONS:
            # The engine wrote nothing: reopen the row so a valid answer can follow.
            if row.status == "deciding":
                row.status, row.decided_by, row.decision, row.note = "pending", None, None, None
                row.updated_at = _now()
                await self._commit(row.tenant_id)
            return None
        if reason == "expired":
            await self._close(row, "expired", AuditAction.APPROVAL_EXPIRED, "engine rejected: expired")
            raise ApprovalError(410, "approval_expired")
        if reason == "not_waiting":
            await self._close(row, "cancelled", AuditAction.APPROVAL_CANCELLED, "engine rejected: not waiting")
            raise ApprovalError(409, "not_waiting")
        if reason == "already_answered" and item is not None:
            await self.finalize_from_engine(row, item)
            await self._commit(row.tenant_id)
            status = item.get("status")
            if status == "expired":
                raise ApprovalError(410, "approval_expired")
            if status == "cancelled":
                raise ApprovalError(409, "not_waiting")
            if str((item.get("answer") or {}).get("principal")) == str(user.id):
                return {"question": item}
            raise ApprovalError(409, "already_answered")
        return None

    # --- finalising -----------------------------------------------------------

    async def _finalize_decided(self, row: ToolApproval, *, decided_by, decision: str, note: Optional[str],
                                engine_item: Optional[dict]) -> None:
        if row.status not in OPEN_STATUSES:
            return
        decided_uid = _uuid(getattr(decided_by, "id", decided_by))
        row.status = "approved" if decision == "approve" else "denied"
        row.decision = decision
        row.decided_by = decided_uid
        if note is not None:
            row.note = note
        row.decided_at = _now()
        row.updated_at = _now()
        row.self_approved = decided_uid is not None and decided_uid == row.requested_by
        actor = await self._actor(decided_by)
        await self._audit(row, AuditAction.APPROVAL_DECIDED, decision, actor,
                          rationale=row.note or f"{row.tool_name or 'tool'} approval: {decision}",
                          extra={"self_approved": row.self_approved})
        await self._commit(row.tenant_id)

    async def finalize_from_engine(self, row: ToolApproval, item: dict) -> ToolApproval:
        """Settle ``row`` from the engine item (decided by whoever the engine recorded; KTD5/KTD11).

        Does not commit. A row that already left ``pending``/``deciding`` is left alone, so no second
        audit entry is ever written.
        """
        if row.status not in OPEN_STATUSES:
            return row
        status = item.get("status")
        if status in ("approved", "denied"):
            dec = item.get("decision") or {}
            principal = _uuid(dec.get("principal"))
            actor_user = await self.db.get(User, principal) if principal is not None else None
            if actor_user is not None and str(actor_user.tenant_id) != str(row.tenant_id):
                actor_user = None
            kind = "approve" if status == "approved" else "deny"
            row.status, row.decision = status, kind
            row.decided_by = actor_user.id if actor_user is not None else None
            row.note = dec.get("note", row.note)
            ms = dec.get("decidedAt")
            row.decided_at = datetime.fromtimestamp(ms / 1000, timezone.utc) if isinstance(ms, (int, float)) else _now()
            row.updated_at = _now()
            row.self_approved = row.decided_by is not None and row.decided_by == row.requested_by
            actor = await self._actor(actor_user, fallback=str(dec.get("principal")))
            await self._audit(row, AuditAction.APPROVAL_DECIDED, kind, actor,
                              rationale=row.note or f"{row.tool_name or 'tool'} approval: {kind}",
                              extra={"self_approved": row.self_approved, "from_engine": True})
        elif status == "answered":
            await self._finalize_answered_from_engine(row, item)
        elif status == "expired":
            await self._close(row, "expired", AuditAction.APPROVAL_EXPIRED, "engine item expired", commit=False)
        elif status == "cancelled":
            await self._close(row, "cancelled", AuditAction.APPROVAL_CANCELLED, "engine item cancelled", commit=False)
        return row

    async def _finalize_answered_from_engine(self, row: ToolApproval, item: dict) -> None:
        ans = item.get("answer") if isinstance(item.get("answer"), dict) else {}
        principal = _uuid(ans.get("principal"))
        actor_user = await self.db.get(User, principal) if principal is not None else None
        if actor_user is not None and str(actor_user.tenant_id) != str(row.tenant_id):
            actor_user = None
        row.status, row.decision = "answered", "answer"
        row.decided_by = actor_user.id if actor_user is not None else None
        ms = ans.get("answeredAt")
        row.decided_at = datetime.fromtimestamp(ms / 1000, timezone.utc) if isinstance(ms, (int, float)) else _now()
        row.updated_at = _now()
        actor = await self._actor(actor_user, fallback=str(ans.get("principal")))
        await self._audit(row, AuditAction.QUESTION_ANSWERED, "answer", actor,
                          rationale="question answered", extra=self._answer_meta(ans, from_engine=True))

    @staticmethod
    def _answer_meta(answer: dict, **extra) -> dict:
        """The answer's shape for the audit entry; free text is never copied into the chain."""
        text = answer.get("text")
        return {"option_ids": list(answer.get("optionIds") or []), "has_text": bool(text),
                "text_length": len(text) if isinstance(text, str) else 0, **extra}

    async def _close(self, row: ToolApproval, status: str, action: AuditAction, why: str,
                     commit: bool = True) -> None:
        if row.status not in OPEN_STATUSES:
            return
        row.status = status
        row.updated_at = _now()
        await self._audit(row, action, status, None, rationale=why)
        if commit:
            await self._commit(row.tenant_id)

    async def expire_overdue(self, tenant_id, now: Optional[datetime] = None,
                             engine: Optional[EngineClient] = None) -> int:
        """Mark ``pending`` rows past ``expires_at`` as ``expired`` (one audit entry each).

        With an ``engine``, ``deciding`` rows older than 60 s are also settled against the engine item
        (KTD5/KTD11) instead of being guessed at.
        """
        now = now or _now()
        res = await self.db.execute(select(ToolApproval).where(
            ToolApproval.tenant_id == _uuid(tenant_id), ToolApproval.status == "pending",
            ToolApproval.expires_at.is_not(None), ToolApproval.expires_at < now))
        rows = list(res.scalars().all())
        for row in rows:
            row.status = "expired"
            row.updated_at = now
            await self._audit(row, AuditAction.APPROVAL_EXPIRED, "expired", None,
                              rationale="approval not decided before its expiry")
        if rows:
            await self._commit(tenant_id)
        if engine is not None:
            stale = await self._open_rows(tenant_id, status="deciding", older_than=now - DECIDING_STALE)
            await self._reconcile_rows(tenant_id, stale, engine, now)
        return len(rows)

    # --- reconcile (KTD11) ----------------------------------------------------

    async def _open_rows(self, tenant_id, *, status: Optional[str] = None,
                         older_than: Optional[datetime] = None) -> list[ToolApproval]:
        q = select(ToolApproval).where(
            ToolApproval.tenant_id == _uuid(tenant_id), ToolApproval.kind.in_(("tool_call", "question")),
            ToolApproval.conversation_id.is_not(None), ToolApproval.tool_task_id.is_not(None),
            ToolApproval.status.in_((status,) if status else OPEN_STATUSES)
        ).order_by(ToolApproval.created_at, ToolApproval.id)
        rows = list((await self.db.execute(q)).scalars().all())
        if older_than is not None:
            rows = [r for r in rows if (_aware(r.updated_at) or _now()) < older_than]
        return rows

    async def reconcile(self, tenant_id, engine: EngineClient, now: Optional[datetime] = None,
                        force: bool = False) -> int:
        """Close or finalise open rows whose engine item says otherwise. Returns rows changed.

        Rate limited to once per tenant per 30 s (in-process) unless ``force``; reads at most 20
        engine conversations per pass; a no-op when the engine is unreachable.
        """
        now = now or _now()
        key = str(_uuid(tenant_id))
        last = _last_reconcile.get(key)
        if not force and last is not None and now - last < RECONCILE_MIN_INTERVAL:
            return 0
        _last_reconcile[key] = now
        return await self._reconcile_rows(tenant_id, await self._open_rows(tenant_id), engine, now)

    async def _reconcile_rows(self, tenant_id, rows: list[ToolApproval], engine: EngineClient,
                              now: datetime) -> int:
        by_conv: dict[UUID, list[ToolApproval]] = {}
        for row in rows:
            by_conv.setdefault(row.conversation_id, []).append(row)
        changed = 0
        for conv_id in list(by_conv)[:RECONCILE_MAX_CONVERSATIONS]:
            conv_rows = by_conv[conv_id]
            conv = await self.db.get(EngineConversation, conv_id)
            if conv is None:
                changed += await self._close_all(conv_rows, "conversation no longer exists")
                continue
            for kind, reader in (("tool_call", "approvals"), ("question", "questions")):
                kind_rows = [r for r in conv_rows if r.kind == kind]
                if not kind_rows:
                    continue
                try:
                    items = await getattr(engine, reader)(conv.session_id, conv.tenant_id, conv.project_id, conv.conversation_id)
                except EngineError as e:
                    if e.status == 404:
                        changed += await self._close_all(kind_rows, "engine conversation not found")
                    continue  # unreachable or failing: change nothing
                except Exception:
                    logger.warning("approval reconcile: engine read failed", exc_info=True)
                    continue
                by_task = {i.get("toolTaskId"): i for i in items}
                for row in kind_rows:
                    item = by_task.get(row.tool_task_id)
                    if item is not None:
                        if item.get("status") in ("approved", "denied", "answered", "expired", "cancelled"):
                            await self.finalize_from_engine(row, item)
                            changed += 1
                    elif (_aware(row.created_at) or now) < now - ORPHAN_GRACE:
                        await self._close(row, "cancelled", AuditAction.APPROVAL_CANCELLED,
                                          "engine item missing", commit=False)
                        changed += 1
        if changed:
            await self._commit(tenant_id)
        return changed

    async def _close_all(self, rows: list[ToolApproval], why: str) -> int:
        for row in rows:
            await self._close(row, "cancelled", AuditAction.APPROVAL_CANCELLED, why, commit=False)
        return len(rows)

    async def mark_closed(self, tenant_id, *, conversation_id=None, run_id: Optional[str] = None,
                          reason: str = "cancelled", commit: bool = True) -> int:
        """Close pending and deciding rows for a root conversation or a run, without an engine call."""
        if conversation_id is None and run_id is None:
            raise ValueError("mark_closed needs a conversation_id or a run_id")
        q = select(ToolApproval).where(
            ToolApproval.tenant_id == _uuid(tenant_id), ToolApproval.status.in_(OPEN_STATUSES))
        if conversation_id is not None:
            q = q.where(ToolApproval.conversation_id == _uuid(conversation_id))
        if run_id is not None:
            q = q.where(ToolApproval.run_id == run_id)
        rows = list((await self.db.execute(q)).scalars().all())
        for row in rows:
            row.status = "cancelled"
            row.updated_at = _now()
            await self._audit(row, AuditAction.APPROVAL_CANCELLED, "cancelled", None,
                              rationale=f"approval closed: {reason}", extra={"reason": reason})
        if rows and commit:
            await self._commit(tenant_id)
        return len(rows)

    # --- audit ----------------------------------------------------------------

    async def _actor(self, who, fallback: Optional[str] = None) -> dict:
        if who is None:
            return {"id": fallback, "role": None, "email": None, "name": None}
        if isinstance(who, User):
            return {"id": str(who.id), "role": who.role, "email": who.email, "name": who.name}
        return {"id": str(who.id), "role": who.role, "email": who.email, "name": getattr(who, "name", None)}

    async def _audit(self, row: ToolApproval, action: AuditAction, decision: str, actor: Optional[dict],
                     *, rationale: str, extra: Optional[dict] = None) -> None:
        meta = {
            "approval_id": str(row.id), "tool_name": row.tool_name, "tool_task_id": row.tool_task_id,
            "conversation_id": str(row.conversation_id) if row.conversation_id else None,
            "run_id": row.run_id, "tool_call_id": row.tool_call_id,
            "actor_id": actor.get("id") if actor else None,
            "actor_role": actor.get("role") if actor else None,
        }
        meta.update(extra or {})
        await self.audit.append(
            tenant_id=row.tenant_id, agent_id=AUDIT_AGENT_ID, action=action, decision=decision,
            rationale=rationale, user_id=_uuid(actor.get("id")) if actor else None,
            actor_email=actor.get("email") if actor else None,
            actor_name=actor.get("name") if actor else None,
            tools_called=[row.tool_name] if row.tool_name else [], extra_metadata=meta)

