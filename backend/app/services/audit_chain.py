"""Per-tenant, signed, append-only audit chain (U1, KTD9).

The chain head lives in ``audit_chain_heads`` and is merely a cache: ``append`` locks it
(``SELECT ... FOR UPDATE``), compares it with the last committed entry, repairs it if they
disagree, then links the new entry to it. A unique ``(tenant_id, seq)`` index on the log makes
a fork impossible even if the lock is bypassed. Entries written before this chain (``seq IS
NULL``) are reported as ``legacy_unchained`` and never rewritten.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance_audit_log import AuditChainHead, ComplianceAuditLog
from app.security.kms import KeyManagementService, get_kms

logger = logging.getLogger(__name__)

# Every field covered by the entry hash and signature. ``user_id`` is deliberately excluded: it
# is a mutable pointer (``ON DELETE SET NULL``), while ``actor_email``/``actor_name`` are the
# snapshot that survives user deletion.
PAYLOAD_FIELDS = (
    "id", "tenant_id", "agent_id", "action", "decision", "rationale", "data_sources",
    "workflow_state", "workflow_id", "session_id", "trace_id", "previous_state", "tools_called",
    "extra_metadata", "retention_until", "created_at", "actor_email", "actor_name",
    "seq", "previous_hash", "signing_key_id",
)


def _uuid(value: Any) -> Optional[UUID]:
    if value is None or isinstance(value, UUID):
        return value
    return UUID(str(value))


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _canonical(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    return value


def _attrs(row: ComplianceAuditLog) -> dict[str, Any]:
    return {name: getattr(row, name) for name in PAYLOAD_FIELDS}


def entry_bytes(row: ComplianceAuditLog) -> bytes:
    """Canonical UTF-8 JSON of the hashable fields of ``row`` (sorted keys, no whitespace)."""
    payload = {name: _canonical(value) for name, value in _attrs(row).items()}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def compute_entry_hash(row: ComplianceAuditLog) -> str:
    return hashlib.sha256(entry_bytes(row)).hexdigest()


class AuditChain:
    """Append to and verify a tenant's signed audit chain."""

    def __init__(self, db: AsyncSession, kms: Optional[KeyManagementService] = None):
        self.db = db
        self._kms = kms

    @property
    def kms(self) -> KeyManagementService:
        if self._kms is None:
            self._kms = get_kms()
        return self._kms

    async def append(
        self,
        *,
        tenant_id,
        agent_id: str,
        action,
        decision: str,
        rationale: str,
        user_id=None,
        actor_email: Optional[str] = None,
        actor_name: Optional[str] = None,
        data_sources: Optional[list[str]] = None,
        workflow_state: Optional[str] = None,
        workflow_id=None,
        session_id=None,
        trace_id: Optional[str] = None,
        previous_state: Optional[str] = None,
        tools_called: Optional[list[str]] = None,
        extra_metadata: Optional[dict] = None,
        retention_until: Optional[datetime] = None,
        retention_years: int = 7,
    ) -> ComplianceAuditLog:
        """Link a new entry to the tenant's head and return it. Does not commit."""
        tenant_uuid = _uuid(tenant_id)
        if tenant_uuid is None:
            raise ValueError("tenant_id is required for chained audit entries")

        await self._ensure_head(tenant_uuid)
        head = await self._lock_head(tenant_uuid)
        last = await self._last_entry(tenant_uuid)
        if (last is None and head.seq != 0) or (
            last is not None and (head.seq != last.seq or head.head_hash != last.entry_hash)
        ):
            logger.info(
                "audit_head_repaired tenant=%s head_seq=%s last_seq=%s",
                tenant_uuid, head.seq, None if last is None else last.seq,
            )
            head = await self.rebuild_head(tenant_uuid)

        now = datetime.now(timezone.utc)
        row = ComplianceAuditLog(
            id=uuid.uuid4(),
            tenant_id=tenant_uuid,
            agent_id=agent_id,
            action=action.value if isinstance(action, Enum) else action,
            decision=decision,
            rationale=rationale,
            data_sources=list(data_sources or []),
            tools_called=list(tools_called or []),
            extra_metadata=dict(extra_metadata or {}),
            workflow_state=workflow_state,
            workflow_id=_uuid(workflow_id),
            session_id=_uuid(session_id),
            trace_id=trace_id,
            previous_state=previous_state,
            user_id=_uuid(user_id),
            actor_email=actor_email,
            actor_name=actor_name,
            seq=head.seq + 1,
            previous_hash=head.head_hash,
            signing_key_id=self.kms.key_id,
            retention_until=retention_until or (now + timedelta(days=retention_years * 365)),
            created_at=now,
        )
        payload = entry_bytes(row)
        row.entry_hash = hashlib.sha256(payload).hexdigest()
        row.signature = base64.b64encode(self.kms.sign(payload)).decode()

        self.db.add(row)
        head.seq = row.seq
        head.head_hash = row.entry_hash
        head.key_id = self.kms.key_id
        await self.db.flush()
        return row

    async def rebuild_head(self, tenant_id) -> AuditChainHead:
        """Recompute the head from the last committed entry and return it."""
        tenant_uuid = _uuid(tenant_id)
        await self._ensure_head(tenant_uuid)
        head = await self._lock_head(tenant_uuid)
        last = await self._last_entry(tenant_uuid)
        head.seq = last.seq if last is not None else 0
        head.head_hash = last.entry_hash if last is not None else None
        head.key_id = last.signing_key_id if last is not None else self.kms.key_id
        await self.db.flush()
        return head

    async def verify_chain(self, tenant_id) -> dict[str, Any]:
        """Recompute hashes, links and signatures; report the first break, if any."""
        tenant_uuid = _uuid(tenant_id)
        rows = (await self.db.execute(
            select(ComplianceAuditLog)
            .where(ComplianceAuditLog.tenant_id == tenant_uuid)
            .order_by(ComplianceAuditLog.seq.asc().nulls_last(), ComplianceAuditLog.created_at.asc())
        )).scalars().all()

        checked = 0
        legacy = 0
        # Retention may have purged a prefix: the chain starts at the first remaining sequence, and
        # that entry's link to the purged one cannot (and need not) be checked.
        expected_seq: Optional[int] = None
        expected_prev: Optional[str] = None
        for row in rows:
            if row.seq is None:
                legacy += 1
                continue
            if expected_seq is None:
                expected_seq = row.seq
                if row.seq != 1:
                    expected_prev = row.previous_hash or None
            reason = self._break_reason(row, expected_seq, expected_prev)
            if reason is not None:
                return {"ok": False, "checked": checked, "legacy_unchained": legacy,
                        "first_break": {"seq": row.seq, "reason": reason}}
            checked += 1
            expected_seq += 1
            expected_prev = row.entry_hash
        return {"ok": True, "checked": checked, "legacy_unchained": legacy, "first_break": None}

    def _break_reason(self, row: ComplianceAuditLog, expected_seq: int,
                      expected_prev: Optional[str]) -> Optional[str]:
        if row.seq != expected_seq:
            return "missing_entry"
        if (row.previous_hash or None) != expected_prev:
            return "previous_hash"
        if compute_entry_hash(row) != row.entry_hash:
            return "entry_hash"
        try:
            signature = base64.b64decode(row.signature)
        except Exception:
            return "signature"
        if not self.kms.verify(entry_bytes(row), signature, key_id=row.signing_key_id):
            return "signature"
        return None

    # -- head helpers --------------------------------------------------------------------

    async def _ensure_head(self, tenant_id: UUID) -> None:
        if await self.db.get(AuditChainHead, tenant_id) is not None:
            return
        try:
            async with self.db.begin_nested():
                self.db.add(AuditChainHead(
                    tenant_id=tenant_id, seq=0, head_hash=None, key_id=self.kms.key_id))
        except IntegrityError:
            pass

    async def _lock_head(self, tenant_id: UUID) -> AuditChainHead:
        return (await self.db.execute(
            select(AuditChainHead)
            .where(AuditChainHead.tenant_id == tenant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )).scalar_one()

    async def _last_entry(self, tenant_id: UUID) -> Optional[ComplianceAuditLog]:
        return (await self.db.execute(
            select(ComplianceAuditLog)
            .where(ComplianceAuditLog.tenant_id == tenant_id, ComplianceAuditLog.seq.isnot(None))
            .order_by(ComplianceAuditLog.seq.desc())
            .limit(1)
        )).scalar_one_or_none()
