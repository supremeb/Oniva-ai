"""Audit Log model with multi-tenancy support."""
from sqlalchemy import Column, String, DateTime, ForeignKey, BigInteger, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, INET

from app.models import Base


class AuditLog(Base):
    """
    Comprehensive audit trail for compliance.
    7-year retention for financial data.
    """
    __tablename__ = 'audit_logs'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True)

    # Multi-tenancy
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey('tenants.id', ondelete='CASCADE'),
        nullable=False,
        index=True
    )

    # Action details
    action = Column(String(100), nullable=False, index=True)
    entity_type = Column(String(50), nullable=True)
    entity_id = Column(UUID(as_uuid=True), nullable=True)

    # Context
    details = Column(JSONB, default=dict)
    ip_address = Column(INET, nullable=True)
    user_agent = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
