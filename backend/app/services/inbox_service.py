"""The "Needs attention" inbox: a read model over the approval ledger and a few existing tables
(Approvals & Policy plan, U7 / KTD8). Nothing is copied; every call reads the sources.

Adapters: ``approval`` / ``review`` / ``question`` (ledger rows, ``pending`` and not expired),
``compliance`` (``manual_review`` executions), ``fleet`` (failed background jobs in the last 72 h),
``deep_work`` (running runs with an ``awaiting_input`` clarification). Visibility is the same RBAC as
deciding: ``approvals:decide`` holders (admin, owner) see the tenant's items, everyone else their own.
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import has_permission
from app.auth.providers.base import AuthUser
from app.models.background_job import BackgroundJob
from app.models.compliance_execution import ComplianceExecution, ComplianceStatus
from app.models.deep_work_run import DeepWorkRun
from app.models.tool_approval import ToolApproval
from app.services.approval_service import ApprovalError, ApprovalService
from app.services.engine_client import EngineClient

logger = logging.getLogger(__name__)

KINDS = ("approval", "review", "question", "compliance", "fleet", "deep_work")
_LEDGER_KIND = {"tool_call": "approval", "review": "review", "question": "question"}
_LEDGER_KIND_REV = {v: k for k, v in _LEDGER_KIND.items()}
FLEET_JOB_TYPES = ("workflow_execution", "background_agent")
FLEET_WINDOW = timedelta(hours=72)
MAX_PER_SOURCE = 500
_SEVERITY = {"critical": "high", "high": "high", "medium": "medium", "low": "low"}


class InvalidInboxQuery(ValueError):
    pass


def parse_kinds(raw: Optional[str]) -> Optional[list[str]]:
    if raw is None or not raw.strip():
        return None
    kinds = [k.strip() for k in raw.split(",") if k.strip()]
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        raise InvalidInboxQuery(f"unknown kinds: {', '.join(bad)}")
    return kinds


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)  # SQLite drops tzinfo
    return dt


def _us(dt: Optional[datetime]) -> int:
    dt = _aware(dt) or datetime.fromtimestamp(0, timezone.utc)
    return int(dt.timestamp() * 1_000_000)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    dt = _aware(dt)
    return dt.isoformat() if dt else None


def _sort_key(item: dict) -> tuple:
    """Due soonest first (items without a due date last), then newest, then id."""
    due = item.get("_due")
    return (1 if due is None else 0, 0 if due is None else _us(due), -_us(item["_created"]), item["id"])


def encode_cursor(key: tuple) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(key)).encode()).decode()


def decode_cursor(raw: str) -> tuple:
    try:
        key = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
        if not (isinstance(key, list) and len(key) == 4):
            raise ValueError
        return (int(key[0]), int(key[1]), int(key[2]), str(key[3]))
    except Exception:
        raise InvalidInboxQuery("invalid cursor") from None


def _public(item: dict) -> dict:
    return {k: v for k, v in item.items() if not k.startswith("_")}


class InboxService:
    def __init__(self, db: AsyncSession, engine: Optional[EngineClient] = None,
                 approvals: Optional[ApprovalService] = None):
        self.db = db
        self.engine = engine
        self.approvals = approvals or ApprovalService(db)

    # --- visibility ---------------------------------------------------------

    @staticmethod
    def _sees_tenant(user: AuthUser) -> bool:
        return has_permission(user.role, "approvals:decide")

    def _ledger_filter(self, user: AuthUser):
        conds = [ToolApproval.tenant_id == UUID(str(user.tenant_id)), ToolApproval.status == "pending"]
        conds.append(or_(ToolApproval.expires_at.is_(None), ToolApproval.expires_at > datetime.now(timezone.utc)))
        if not self._sees_tenant(user):
            conds.append(ToolApproval.requested_by == UUID(str(user.id)))
        return conds

    # --- adapters -----------------------------------------------------------

    async def _ledger_items(self, user: AuthUser, kinds: Optional[list[str]]) -> list[dict]:
        ledger_kinds = [_LEDGER_KIND_REV[k] for k in (kinds or KINDS) if k in _LEDGER_KIND_REV]
        if not ledger_kinds:
            return []
        q = select(ToolApproval).where(*self._ledger_filter(user), ToolApproval.kind.in_(ledger_kinds)) \
            .order_by(ToolApproval.created_at.desc()).limit(MAX_PER_SOURCE)
        out = []
        for row in (await self.db.execute(q)).scalars().all():
            out.append(await self._ledger_item(user, row))
        return out

    async def _ledger_item(self, user: AuthUser, row: ToolApproval) -> dict:
        kind = _LEDGER_KIND.get(row.kind, "approval")
        actions: list[dict] = []
        href = f"/chat/{row.conversation_id}" if row.conversation_id else "/chat"
        if row.kind == "tool_call" and row.conversation_id and row.tool_task_id:
            action = {
                "type": "decide",
                "endpoint": f"/api/v1/engine/conversations/{row.conversation_id}/approvals/{row.tool_task_id}/decision",
                "requiresRole": "admin" if row.approver_role == "admin" else "member",
                "allowed": True,
            }
            try:
                await self.approvals.can_decide(user, row)
                actions.append(action)
            except ApprovalError as e:
                if e.code == "self_approval_forbidden":  # may decide in principle, not this one
                    actions.append({**action, "allowed": False, "disabledReason": e.code})
        high = row.approver_role == "admin" or row.rule == "rule_of_two"
        title = row.tool_name or {"review": "Output review", "question": "Question"}.get(row.kind, "Approval")
        return {
            "id": f"{kind}:{row.id}", "kind": kind,
            "title": f"Approve {title}" if row.kind == "tool_call" else str(title),
            "summary": row.reason or "",
            "severity": "high" if high else "medium",
            "createdAt": _iso(row.created_at), "dueAt": _iso(row.expires_at),
            "source": {"type": "tool_approval", "id": str(row.id)},
            "actions": actions, "href": href,
            "_created": row.created_at, "_due": _aware(row.expires_at),
        }

    async def _compliance_items(self, user: AuthUser) -> list[dict]:
        if not self._sees_tenant(user):
            return []
        q = select(ComplianceExecution).where(
            ComplianceExecution.tenant_id == UUID(str(user.tenant_id)),
            ComplianceExecution.status == ComplianceStatus.MANUAL_REVIEW.value,
            ComplianceExecution.reviewed_at.is_(None),
        ).order_by(ComplianceExecution.created_at.desc()).limit(MAX_PER_SOURCE)
        out = []
        for ex in (await self.db.execute(q)).scalars().all():
            out.append({
                "id": f"compliance:{ex.id}", "kind": "compliance",
                "title": f"Review {ex.template_name or ex.template_id}",
                "summary": f"{ex.subject_type} {ex.subject_name or ex.subject_id} needs manual review",
                "severity": _SEVERITY.get(ex.risk_level or "", "medium"),
                "createdAt": _iso(ex.created_at), "dueAt": None,
                "source": {"type": "compliance_execution", "id": str(ex.id)},
                "actions": [], "href": "/compliance",
                "_created": ex.created_at, "_due": None,
            })
        return out

    def _fleet_filter(self, user: AuthUser):
        since = datetime.now(timezone.utc) - FLEET_WINDOW
        conds = [BackgroundJob.tenant_id == UUID(str(user.tenant_id)), BackgroundJob.status == "failed",
                 BackgroundJob.job_type.in_(FLEET_JOB_TYPES),
                 func.coalesce(BackgroundJob.completed_at, BackgroundJob.created_at) >= since]
        if not self._sees_tenant(user):
            conds.append(BackgroundJob.user_id == UUID(str(user.id)))
        return conds

    async def _fleet_items(self, user: AuthUser) -> list[dict]:
        q = select(BackgroundJob).where(*self._fleet_filter(user)) \
            .order_by(BackgroundJob.created_at.desc()).limit(MAX_PER_SOURCE)
        out = []
        for job in (await self.db.execute(q)).scalars().all():
            out.append({
                "id": f"fleet:{job.id}", "kind": "fleet", "title": "Run failed",
                "summary": (job.error or "A background run failed")[:300],
                "severity": "medium",
                "createdAt": _iso(job.completed_at or job.created_at), "dueAt": None,
                "source": {"type": "background_job", "id": str(job.id)},
                "actions": [], "href": "/fleet",
                "_created": job.completed_at or job.created_at, "_due": None,
            })
        return out

    def _dw_filter(self, user: AuthUser):
        return [DeepWorkRun.tenant_id == UUID(str(user.tenant_id)), DeepWorkRun.user_id == UUID(str(user.id)),
                DeepWorkRun.status == "running", DeepWorkRun.awaiting_input.is_not(None)]

    async def _deep_work_items(self, user: AuthUser) -> list[dict]:
        q = select(DeepWorkRun).where(*self._dw_filter(user)) \
            .order_by(DeepWorkRun.updated_at.desc()).limit(MAX_PER_SOURCE)
        out = []
        for run in (await self.db.execute(q)).scalars().all():
            awaiting = run.awaiting_input
            if awaiting is None or awaiting == {}:  # JSON null stored as the value, not SQL NULL
                continue
            qs = awaiting.get("questions") if isinstance(awaiting, dict) else None
            first = (qs[0].get("text") or qs[0].get("question")) if isinstance(qs, list) and qs and isinstance(qs[0], dict) else None
            out.append({
                "id": f"deep_work:{run.id}", "kind": "deep_work", "title": "Deep Work needs your answer",
                "summary": first or (run.query or "")[:300], "severity": "medium",
                "createdAt": _iso(run.updated_at), "dueAt": None,
                "source": {"type": "deep_work_run", "id": str(run.id)},
                "actions": [], "href": f"/chat?deepWork={run.id}",
                "_created": run.updated_at, "_due": None,
            })
        return out

    # --- reads --------------------------------------------------------------

    async def _prepare(self, user: AuthUser) -> None:
        """Lazy sweep then reconcile (KTD11); both are best effort and never fail the read."""
        tenant = user.tenant_id
        try:
            await self.approvals.expire_overdue(tenant, engine=self.engine)
            if self.engine is not None:
                await self.approvals.reconcile(tenant, self.engine)
        except Exception:
            logger.warning("inbox sweep/reconcile failed", exc_info=True)
            await self.db.rollback()

    async def list_for_user(self, user: AuthUser, *, kinds: Optional[list[str]] = None, limit: int = 50,
                            cursor: Optional[str] = None) -> tuple[list[dict], Optional[str]]:
        await self._prepare(user)
        want = set(kinds) if kinds else set(KINDS)
        items = await self._ledger_items(user, kinds)
        if "compliance" in want:
            items += await self._compliance_items(user)
        if "fleet" in want:
            items += await self._fleet_items(user)
        if "deep_work" in want:
            items += await self._deep_work_items(user)
        items.sort(key=_sort_key)
        if cursor:
            after = decode_cursor(cursor)
            items = [i for i in items if _sort_key(i) > after]
        page = items[:limit]
        nxt = encode_cursor(_sort_key(page[-1])) if len(items) > limit and page else None
        return [_public(i) for i in page], nxt

    async def count_for_user(self, user: AuthUser) -> dict:
        by_kind = {k: 0 for k in KINDS}
        res = await self.db.execute(
            select(ToolApproval.kind, func.count()).where(*self._ledger_filter(user)).group_by(ToolApproval.kind))
        for kind, n in res.all():
            by_kind[_LEDGER_KIND.get(kind, "approval")] += int(n)
        if self._sees_tenant(user):
            by_kind["compliance"] = int((await self.db.execute(
                select(func.count()).select_from(ComplianceExecution).where(
                    ComplianceExecution.tenant_id == UUID(str(user.tenant_id)),
                    ComplianceExecution.status == ComplianceStatus.MANUAL_REVIEW.value,
                    ComplianceExecution.reviewed_at.is_(None)))).scalar() or 0)
        by_kind["fleet"] = int((await self.db.execute(
            select(func.count()).select_from(BackgroundJob).where(*self._fleet_filter(user)))).scalar() or 0)
        by_kind["deep_work"] = int((await self.db.execute(
            select(func.count()).select_from(DeepWorkRun).where(*self._dw_filter(user)))).scalar() or 0)
        return {"total": sum(by_kind.values()), "byKind": by_kind}
