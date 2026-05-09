"""
Cryptographic Audit Logging Service

Provides tamper-proof audit logging for regulatory compliance.
All agent decisions are signed with RSA-2048 for integrity verification.

Based on Section 3.5 - Compliance & Audit requirements.

Features:
- RSA-2048 digital signatures on all audit entries
- Dual storage: PostgreSQL (7-year retention) + session filesystem
- Signature verification for tampering detection
- Replay mode support for regulatory audits
- Export capabilities (JSON, CSV)
"""

import json
import base64
import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend

from app.config import get_settings

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
# Key Management
# =============================================================================

class AuditKeyManager:
    """
    Manages cryptographic keys for audit signing.

    In production, keys should be stored in a secure key vault
    (e.g., AWS KMS, HashiCorp Vault, Azure Key Vault).
    """

    def __init__(
        self,
        private_key_path: Optional[Path] = None,
        public_key_path: Optional[Path] = None,
    ):
        self._private_key = None
        self._public_key = None
        self._private_key_path = private_key_path
        self._public_key_path = public_key_path

    def generate_key_pair(self) -> tuple:
        """Generate a new RSA-2048 key pair."""
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )
        public_key = private_key.public_key()

        self._private_key = private_key
        self._public_key = public_key

        return private_key, public_key

    def load_keys(self) -> None:
        """Load keys from configured paths."""
        if self._private_key_path and self._private_key_path.exists():
            with open(self._private_key_path, "rb") as f:
                self._private_key = serialization.load_pem_private_key(
                    f.read(),
                    password=None,
                    backend=default_backend(),
                )
                self._public_key = self._private_key.public_key()
        else:
            # Generate new keys if not found
            logger.warning("No audit keys found, generating new key pair")
            self.generate_key_pair()

    def save_keys(self) -> None:
        """Save keys to configured paths."""
        if self._private_key_path:
            self._private_key_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._private_key_path, "wb") as f:
                f.write(
                    self._private_key.private_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PrivateFormat.PKCS8,
                        encryption_algorithm=serialization.NoEncryption(),
                    )
                )

        if self._public_key_path:
            self._public_key_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._public_key_path, "wb") as f:
                f.write(
                    self._public_key.public_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PublicFormat.SubjectPublicKeyInfo,
                    )
                )

    @property
    def private_key(self):
        if self._private_key is None:
            self.load_keys()
        return self._private_key

    @property
    def public_key(self):
        if self._public_key is None:
            self.load_keys()
        return self._public_key


# =============================================================================
# Audit Logger
# =============================================================================

class ComplianceAuditLogger:
    """
    Cryptographically-signed audit logging for regulatory compliance.

    Features:
    - Tamper-proof entries with RSA-2048 digital signatures
    - 7-year retention policy for SEC/FINRA compliance
    - Dual storage: PostgreSQL + session filesystem
    - Blockchain-like chaining (each entry references previous hash)
    - Signature verification for tampering detection
    """

    def __init__(
        self,
        db: Optional[AsyncSession] = None,
        key_manager: Optional[AuditKeyManager] = None,
        retention_years: int = 7,
    ):
        self.db = db
        self.key_manager = key_manager or self._get_default_key_manager()
        self.retention_years = retention_years
        self._last_entry_hash: Optional[str] = None

    def _get_default_key_manager(self) -> AuditKeyManager:
        """Get or create default key manager."""
        settings = get_settings()
        base_path = Path(getattr(settings, 'SESSION_BASE_PATH', './sessions'))
        keys_path = base_path / ".audit_keys"

        return AuditKeyManager(
            private_key_path=keys_path / "private_key.pem",
            public_key_path=keys_path / "public_key.pem",
        )

    def _sign_entry(self, entry: AuditEntry) -> str:
        """Create RSA-2048 digital signature for an entry."""
        entry_json = json.dumps(entry.to_signable_dict(), sort_keys=True)

        signature = self.key_manager.private_key.sign(
            entry_json.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )

        return base64.b64encode(signature).decode()

    def _verify_signature(self, entry: AuditEntry) -> bool:
        """Verify an entry's signature."""
        if not entry.signature:
            return False

        try:
            entry_json = json.dumps(entry.to_signable_dict(), sort_keys=True)
            signature = base64.b64decode(entry.signature)

            self.key_manager.public_key.verify(
                signature,
                entry_json.encode(),
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
            return True
        except Exception as e:
            logger.warning(f"Signature verification failed: {e}")
            return False

    def _compute_entry_hash(self, entry: AuditEntry) -> str:
        """Compute SHA-256 hash of an entry for chaining."""
        entry_json = json.dumps(entry.to_full_dict(), sort_keys=True)
        return hashlib.sha256(entry_json.encode()).hexdigest()

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
    ) -> AuditEntry:
        """
        Log an agent decision with cryptographic signature.

        Args:
            agent_id: ID of the agent making the decision
            decision: The decision made
            rationale: Reasoning behind the decision
            data_sources: Data sources consulted
            workflow_state: Current workflow state
            session_id: Session ID
            workflow_id: Workflow ID
            user_id: User ID
            tenant_id: Tenant ID
            session_path: Path for filesystem logging

        Returns:
            Signed audit entry
        """
        # Create entry
        entry = AuditEntry(
            agent_id=agent_id,
            decision=decision,
            rationale=rationale,
            data_sources=data_sources,
            workflow_state=workflow_state,
            session_id=session_id,
            workflow_id=workflow_id,
            user_id=user_id,
            tenant_id=tenant_id,
            previous_entry_hash=self._last_entry_hash,
        )

        # Sign the entry
        entry.signature = self._sign_entry(entry)

        # Update chain hash
        self._last_entry_hash = self._compute_entry_hash(entry)

        # Store in PostgreSQL (7-year retention)
        if self.db:
            await self._store_in_database(entry)

        # Append to session audit log
        if session_path:
            await self._append_to_filesystem(entry, session_path)

        logger.info(
            f"Audit entry logged: agent={agent_id}, decision={decision}, "
            f"state={workflow_state}"
        )

        return entry

    async def _store_in_database(self, entry: AuditEntry) -> None:
        """Store audit entry in PostgreSQL."""
        from app.models.compliance_audit_log import ComplianceAuditLog

        retention_until = datetime.utcnow() + timedelta(days=self.retention_years * 365)

        # Compute entry hash for chain integrity
        entry_hash = self._compute_entry_hash(entry)

        db_entry = ComplianceAuditLog(
            id=UUID(entry.id),
            agent_id=entry.agent_id,
            decision=entry.decision,
            rationale=entry.rationale,
            data_sources=entry.data_sources,
            workflow_state=entry.workflow_state,
            workflow_id=UUID(entry.workflow_id) if entry.workflow_id else None,
            session_id=UUID(entry.session_id) if entry.session_id else None,
            user_id=UUID(entry.user_id) if entry.user_id else None,
            tenant_id=UUID(entry.tenant_id) if entry.tenant_id else None,
            action='agent_decision',  # Default action type
            signature=entry.signature,
            previous_hash=entry.previous_entry_hash,
            entry_hash=entry_hash,
            tools_called=[],  # Set by caller if needed
            metadata={},
            retention_until=retention_until,
        )

        self.db.add(db_entry)
        await self.db.commit()

    async def _append_to_filesystem(
        self,
        entry: AuditEntry,
        session_path: Path,
    ) -> None:
        """Append audit entry to session filesystem."""
        import aiofiles

        logs_dir = session_path / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        audit_log_path = logs_dir / "audit.jsonl"

        async with aiofiles.open(audit_log_path, "a") as f:
            await f.write(json.dumps(entry.to_full_dict()) + "\n")

    async def verify_entry(
        self,
        entry_id: str,
    ) -> VerificationResult:
        """
        Verify an audit entry's signature for tampering detection.

        Args:
            entry_id: ID of the entry to verify

        Returns:
            Verification result
        """
        if not self.db:
            return VerificationResult(
                is_valid=False,
                entry_id=entry_id,
                message="Database not available for verification",
            )

        from app.models.compliance_audit_log import ComplianceAuditLog

        # Load entry from database
        stmt = select(ComplianceAuditLog).where(
            ComplianceAuditLog.id == UUID(entry_id)
        )
        result = await self.db.execute(stmt)
        db_entry = result.scalar_one_or_none()

        if not db_entry:
            return VerificationResult(
                is_valid=False,
                entry_id=entry_id,
                message="Entry not found",
            )

        # Reconstruct entry
        entry = AuditEntry(
            id=str(db_entry.id),
            agent_id=db_entry.agent_id,
            decision=db_entry.decision,
            rationale=db_entry.rationale,
            data_sources=db_entry.data_sources or [],
            workflow_state=db_entry.workflow_state,
            workflow_id=str(db_entry.workflow_id) if db_entry.workflow_id else None,
            session_id=str(db_entry.session_id) if db_entry.session_id else None,
            user_id=str(db_entry.user_id) if db_entry.user_id else None,
            tenant_id=str(db_entry.tenant_id) if db_entry.tenant_id else None,
            timestamp=db_entry.created_at,
            signature=db_entry.signature,
            previous_entry_hash=db_entry.previous_hash,
        )

        # Verify signature
        is_valid = self._verify_signature(entry)

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

        from app.models.compliance_audit_log import ComplianceAuditLog

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
        db_entries = result.scalars().all()

        return [
            AuditEntry(
                id=str(e.id),
                agent_id=e.agent_id,
                decision=e.decision,
                rationale=e.rationale,
                data_sources=e.data_sources or [],
                workflow_state=e.workflow_state,
                workflow_id=str(e.workflow_id) if e.workflow_id else None,
                session_id=str(e.session_id) if e.session_id else None,
                user_id=str(e.user_id) if e.user_id else None,
                tenant_id=str(e.tenant_id) if e.tenant_id else None,
                timestamp=e.created_at,
                signature=e.signature,
                previous_entry_hash=e.previous_hash,
            )
            for e in db_entries
        ]

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
# Singleton Instance
# =============================================================================

_audit_logger: Optional[ComplianceAuditLogger] = None


def get_audit_logger(db: Optional[AsyncSession] = None) -> ComplianceAuditLogger:
    """Get the audit logger instance."""
    global _audit_logger

    if _audit_logger is None or db is not None:
        _audit_logger = ComplianceAuditLogger(db=db)

    return _audit_logger


# =============================================================================
# Convenience Functions
# =============================================================================

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
