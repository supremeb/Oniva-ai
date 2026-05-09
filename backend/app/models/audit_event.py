"""Audit event model for compliance logging."""
from typing import Dict, Any

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID as PGUUID

from app.models import Base


class AuditEvent(Base):
    """
    Immutable audit event for compliance logging.

    Events are automatically created by database triggers on CUD operations.
    Cannot be modified or deleted after creation.
    """
    __tablename__ = "audit_events"

    id = Column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())

    # Tenant isolation
    tenant_id = Column(PGUUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)

    # Actor
    user_id = Column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    user_email = Column(String(255), nullable=True)

    # Event details
    action = Column(String(50), nullable=False, index=True)  # CREATE, UPDATE, DELETE, EXECUTE
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(PGUUID(as_uuid=True), nullable=False)
    resource_name = Column(String(255), nullable=True)

    # Change data (sanitized before storage)
    changes = Column(JSONB, nullable=True)
    event_metadata = Column(JSONB, server_default='{}', nullable=False)

    # Request context
    ip_address = Column(INET, nullable=True)
    user_agent = Column(Text, nullable=True)

    # Timestamp
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "user_id": str(self.user_id) if self.user_id else None,
            "user_email": self.user_email,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": str(self.resource_id),
            "resource_name": self.resource_name,
            "changes": self.changes,
            "metadata": self.event_metadata,
            "ip_address": str(self.ip_address) if self.ip_address else None,
            "user_agent": self.user_agent,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def to_csv_row(self) -> Dict[str, str]:
        """Convert to flat dict for CSV export (excludes sensitive data)."""
        return {
            "id": str(self.id),
            "timestamp": self.created_at.isoformat() if self.created_at else "",
            "user_email": self.user_email or "",
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": str(self.resource_id),
            "resource_name": self.resource_name or "",
            "ip_address": str(self.ip_address) if self.ip_address else "",
        }
