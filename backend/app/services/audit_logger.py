"""
Cryptographic Audit Logging Service

Provides tamper-proof audit logging for regulatory compliance. Every agent decision is linked
into the per-tenant, signed audit chain (:mod:`app.services.audit_chain`) and signed through the
configured KMS (:mod:`app.security.kms`).

Based on Section 3.5 - Compliance & Audit requirements.

Features:
- Per-tenant SHA-256 hash chain with RSA-2048 signatures
- 7-year retention for SEC/FINRA compliance
- Optional session filesystem mirror
- Signature verification for tampering detection
- Export capabilities (JSON, CSV)
"""

import json
import base64
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance_audit_log import AuditAction, ComplianceAuditLog
from app.security.kms import get_kms
from app.services.audit_chain import AuditChain, entry_bytes

logger = logging.getLogger(__name__)


# =============================================================================
# Models
# =============================================================================

class AuditEntry(BaseModel):
    """Represents a single audit log entry."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    agent_id: str
    decision: str
    rationale: str
    data_sources: List[str] = []
    workflow_state: str
    workflow_id: Optional[str] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    signature: Optional[str] = None
    previous_entry_hash: Optional[str] = None  # Blockchain-like chain

    def to_signable_dict(self) -> Dict[str, Any]:
        """Get dictionary for signing (excludes signature field)."""
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "decision": self.decision,
            "rationale": self.rationale,
            "data_sources": self.data_sources,
            "workflow_state": self.workflow_state,
            "workflow_id": self.workflow_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp.isoformat(),
            "previous_entry_hash": self.previous_entry_hash,
        }

    def to_full_dict(self) -> Dict[str, Any]:
        """Get full dictionary including signature."""
        data = self.to_signable_dict()
        data["signature"] = self.signature
        return data


class VerificationResult(BaseModel):
    """Result of signature verification."""
    is_valid: bool
    entry_id: str
    message: str
    verified_at: datetime = Field(default_factory=datetime.utcnow)


class AuditExport(BaseModel):
    """Exported audit data."""
    entries: List[AuditEntry]
    export_format: str
    exported_at: datetime = Field(default_factory=datetime.utcnow)
    total_entries: int
    date_range_start: Optional[datetime] = None
    date_range_end: Optional[datetime] = None


# =============================================================================
# Audit Logger
# =============================================================================

def _row_to_entry(row: ComplianceAuditLog) -> AuditEntry:
    return AuditEntry(
        id=str(row.id),
        agent_id=row.agent_id,
        decision=row.decision,
        rationale=row.rationale,
        data_sources=row.data_sources or [],
        workflow_state=row.workflow_state,
        workflow_id=str(row.workflow_id) if row.workflow_id else None,
        session_id=str(row.session_id) if row.session_id else None,
        user_id=str(row.user_id) if row.user_id else None,
        tenant_id=str(row.tenant_id) if row.tenant_id else None,
        timestamp=row.created_at,
        signature=row.signature,
        previous_entry_hash=row.previous_hash,
    )


class ComplianceAuditLogger:
    """
    Cryptographically-signed audit logging for regulatory compliance.

    Writes go through :class:`~app.services.audit_chain.AuditChain`, which gives each tenant a
    gap-free, signed sequence. Verification recomputes the entry hash and checks the signature
    with the key that signed it, so rotated keys still verify old entries.
    """

    def __init__(
        self,
        db: Optional[AsyncSession] = None,
        retention_years: int = 7,
    ):
        self.db = db
        self.retention_years = retention_years
        self.chain = AuditChain(db) if db is not None else None

    async def log_agent_decision(
        self,
        agent_id: str,
        decision: str,
        rationale: str,
        data_sources: List[str],
        workflow_state: str,
        session_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        session_path: Optional[Path] = None,
        action: Union[AuditAction, str] = AuditAction.AGENT_DECISION,
        actor_email: Optional[str] = None,
        actor_name: Optional[str] = None,
        tools_called: Optional[List[str]] = None,
        extra: Optional[Dict[str, Any]] = None,
        **extra_kwargs: Any,
    ) -> AuditEntry:
        """
        Log an agent decision as the next signed entry in the tenant's audit chain.

        ``tenant_id`` is required - chaining is per tenant. The returned ``AuditEntry`` is a
        convenience view of the stored row (callers historically ignored it).
        """
        if self.chain is None:
            raise ValueError("A database session is required to log chained audit entries")

        metadata = dict(extra or {})
        metadata.update(extra_kwargs)

        row = await self.chain.append(
            tenant_id=tenant_id,
            agent_id=agent_id,
            action=action,
            decision=decision,
            rationale=rationale,
            user_id=user_id,
            actor_email=actor_email,
            actor_name=actor_name,
            data_sources=data_sources,
            workflow_state=workflow_state,
            workflow_id=workflow_id,
            session_id=session_id,
            tools_called=tools_called,
            extra_metadata=metadata,
            retention_years=self.retention_years,
        )

        entry = _row_to_entry(row)

        if session_path:
            await self._append_to_filesystem(entry, session_path)

        logger.info(
            "Audit entry logged: agent=%s, decision=%s, state=%s, seq=%s",
            agent_id, decision, workflow_state, row.seq,
        )

        return entry

    async def _append_to_filesystem(self, entry: AuditEntry, session_path: Path) -> None:
        """Append audit entry to session filesystem."""
        import aiofiles

        logs_dir = session_path / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        audit_log_path = logs_dir / "audit.jsonl"

        async with aiofiles.open(audit_log_path, "a") as f:
            await f.write(json.dumps(entry.to_full_dict()) + "\n")

    async def verify_entry(self, entry_id: str) -> VerificationResult:
        """
        Verify an audit entry against its stored hash and signature.

        Entries written before the audit chain (``signing_key_id IS NULL``) cannot be verified.
        """
        if not self.db:
            return VerificationResult(
                is_valid=False,
                entry_id=entry_id,
                message="Database not available for verification",
            )

        row = await self.db.get(ComplianceAuditLog, UUID(entry_id))
        if row is None:
            return VerificationResult(is_valid=False, entry_id=entry_id, message="Entry not found")

        if not row.signing_key_id:
            return VerificationResult(
                is_valid=False,
                entry_id=entry_id,
                message="Legacy entry predates the signed audit chain; not verifiable",
            )

        try:
            signature = base64.b64decode(row.signature)
        except Exception:
            return VerificationResult(
                is_valid=False, entry_id=entry_id, message="Malformed signature encoding")

        is_valid = get_kms().verify(entry_bytes(row), signature, key_id=row.signing_key_id)
        return VerificationResult(
            is_valid=is_valid,
            entry_id=entry_id,
            message="Signature valid" if is_valid else "Signature verification failed",
        )

    async def query_logs(
        self,
        workflow_id: Optional[str] = None,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[AuditEntry]:
        """
        Query audit logs with filters.

        Args:
            workflow_id: Filter by workflow
            session_id: Filter by session
            agent_id: Filter by agent
            date_from: Start date
            date_to: End date
            tenant_id: Filter by tenant
            limit: Max entries to return
            offset: Pagination offset

        Returns:
            List of audit entries
        """
        if not self.db:
            return []

        conditions = []

        if workflow_id:
            conditions.append(ComplianceAuditLog.workflow_id == UUID(workflow_id))
        if session_id:
            conditions.append(ComplianceAuditLog.session_id == session_id)
        if agent_id:
            conditions.append(ComplianceAuditLog.agent_id == agent_id)
        if date_from:
            conditions.append(ComplianceAuditLog.created_at >= date_from)
        if date_to:
            conditions.append(ComplianceAuditLog.created_at <= date_to)
        if tenant_id:
            conditions.append(ComplianceAuditLog.tenant_id == UUID(tenant_id))

        stmt = (
            select(ComplianceAuditLog)
            .where(and_(*conditions) if conditions else True)
            .order_by(ComplianceAuditLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        result = await self.db.execute(stmt)
        return [_row_to_entry(e) for e in result.scalars().all()]

    async def export_logs(
        self,
        export_format: str = "json",
        **query_kwargs,
    ) -> Union[str, bytes]:
        """
        Export audit logs in specified format.

        Args:
            export_format: 'json' or 'csv'
            **query_kwargs: Query filters

        Returns:
            Exported data as string (JSON/CSV)
        """
        entries = await self.query_logs(**query_kwargs)

        if export_format == "json":
            return json.dumps(
                [e.to_full_dict() for e in entries],
                indent=2,
                default=str,
            )

        elif export_format == "csv":
            import csv
            import io

            output = io.StringIO()
            writer = csv.writer(output)

            # Header
            writer.writerow([
                "id", "timestamp", "agent_id", "decision", "rationale",
                "workflow_state", "workflow_id", "session_id", "signature",
            ])

            # Rows
            for entry in entries:
                writer.writerow([
                    entry.id,
                    entry.timestamp.isoformat(),
                    entry.agent_id,
                    entry.decision,
                    entry.rationale,
                    entry.workflow_state,
                    entry.workflow_id,
                    entry.session_id,
                    entry.signature[:20] + "..." if entry.signature else "",
                ])

            return output.getvalue()

        else:
            raise ValueError(f"Unsupported export format: {export_format}")


# =============================================================================
# Convenience Functions
# =============================================================================

def get_audit_logger(db: Optional[AsyncSession] = None) -> ComplianceAuditLogger:
    """Build an audit logger bound to ``db`` (never a shared, session-swapping singleton)."""
    return ComplianceAuditLogger(db=db)


async def log_decision(
    agent_id: str,
    decision: str,
    rationale: str,
    workflow_state: str,
    db: AsyncSession,
    data_sources: Optional[List[str]] = None,
    **kwargs,
) -> AuditEntry:
    """
    Convenience function to log an agent decision.

    This is the main entry point for logging from agents.
    """
    audit_logger = get_audit_logger(db)

    return await audit_logger.log_agent_decision(
        agent_id=agent_id,
        decision=decision,
        rationale=rationale,
        data_sources=data_sources or [],
        workflow_state=workflow_state,
        **kwargs,
    )
