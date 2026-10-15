"""Add compliance audit tables for Phase 3

Revision ID: 024_add_compliance_audit_tables
Revises: 023_add_accuracy_to_versions
Create Date: 2026-01-10

Part of Phase 3: State Machine & Compliance
Adds tables for:
- compliance_audit_logs: Cryptographically signed audit entries (7-year retention)
- workflow_state_logs: State machine transition history
- human_review_tickets: Human-in-loop review queue
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '024_add_compliance_audit_tables'
down_revision = '023_add_accuracy_to_versions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ==========================================================================
    # Table: compliance_audit_logs
    # Cryptographically signed audit entries for regulatory compliance
    # ==========================================================================
    op.create_table(
        'compliance_audit_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('tenants.id', ondelete='CASCADE'),
                  nullable=False, index=True),

        # Agent/Actor identification
        sa.Column('agent_id', sa.String(100), nullable=False, index=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'),
                  nullable=True),

        # Session context
        sa.Column('session_id', postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column('workflow_id', postgresql.UUID(as_uuid=True), nullable=True, index=True),

        # Action details
        sa.Column('action', sa.String(50), nullable=False, index=True),
        sa.Column('decision', sa.String(255), nullable=False),
        sa.Column('rationale', sa.Text, nullable=False),

        # Data lineage
        sa.Column('data_sources', postgresql.ARRAY(sa.String), nullable=False,
                  server_default='{}'),

        # State machine context
        sa.Column('workflow_state', sa.String(50), nullable=True),
        sa.Column('previous_state', sa.String(50), nullable=True),
        sa.Column('tools_called', postgresql.ARRAY(sa.String), nullable=False,
                  server_default='{}'),

        # Cryptographic integrity
        sa.Column('signature', sa.Text, nullable=False),  # RSA-2048 signature (base64)
        sa.Column('previous_hash', sa.String(64), nullable=True),  # SHA-256 of previous entry
        sa.Column('entry_hash', sa.String(64), nullable=False),  # SHA-256 of this entry

        # Additional context
        sa.Column('metadata', postgresql.JSONB, nullable=False, server_default='{}'),

        # Retention (7 years for SEC/FINRA)
        sa.Column('retention_until', sa.DateTime(timezone=True), nullable=False, index=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False, index=True),
    )

    # Create indexes for compliance_audit_logs
    op.create_index(
        'ix_compliance_audit_logs_session_time',
        'compliance_audit_logs',
        ['session_id', 'created_at']
    )
    op.create_index(
        'ix_compliance_audit_logs_workflow_time',
        'compliance_audit_logs',
        ['workflow_id', 'created_at']
    )
    op.create_index(
        'ix_compliance_audit_logs_agent_time',
        'compliance_audit_logs',
        ['agent_id', 'created_at']
    )
    op.create_index(
        'ix_compliance_audit_logs_entry_hash',
        'compliance_audit_logs',
        ['entry_hash']
    )

    # ==========================================================================
    # Table: workflow_state_logs
    # Tracks all state transitions for workflow replay
    # ==========================================================================
    op.create_table(
        'workflow_state_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('tenants.id', ondelete='CASCADE'),
                  nullable=False, index=True),

        # Workflow context
        sa.Column('workflow_id', postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), nullable=False, index=True),

        # State transition
        sa.Column('from_state', sa.String(50), nullable=False),
        sa.Column('to_state', sa.String(50), nullable=False),
        sa.Column('reason', sa.Text, nullable=True),

        # Context at transition
        sa.Column('tools_called_in_state', sa.Integer, nullable=False, server_default='0'),
        sa.Column('context_snapshot', postgresql.JSONB, nullable=False, server_default='{}'),

        # Timing
        sa.Column('transition_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False, index=True),
    )

    # Create indexes for workflow_state_logs
    op.create_index(
        'ix_workflow_state_logs_workflow',
        'workflow_state_logs',
        ['workflow_id', 'transition_at']
    )
    op.create_index(
        'ix_workflow_state_logs_session',
        'workflow_state_logs',
        ['session_id', 'transition_at']
    )

    # ==========================================================================
    # Table: human_review_tickets
    # Human-in-loop review queue for high-risk decisions
    # ==========================================================================
    op.create_table(
        'human_review_tickets',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('tenants.id', ondelete='CASCADE'),
                  nullable=False, index=True),

        # Context
        sa.Column('workflow_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('agent_id', sa.String(100), nullable=False),

        # Requestor
        sa.Column('requested_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),

        # Review details
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=False),
        sa.Column('risk_level', sa.String(20), nullable=False, index=True),
        sa.Column('priority', sa.String(20), nullable=False, server_default='normal'),

        # What needs review
        sa.Column('review_type', sa.String(50), nullable=False),
        sa.Column('review_data', postgresql.JSONB, nullable=False, server_default='{}'),

        # Related audit entry
        sa.Column('audit_log_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('compliance_audit_logs.id', ondelete='SET NULL'),
                  nullable=True),

        # Status
        sa.Column('status', sa.String(20), nullable=False, server_default='pending', index=True),

        # Assignment
        sa.Column('assigned_to', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('assigned_at', sa.DateTime(timezone=True), nullable=True),

        # Resolution
        sa.Column('reviewed_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decision', sa.String(20), nullable=True),
        sa.Column('review_notes', sa.Text, nullable=True),

        # SLA tracking
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sla_breached', sa.String(5), nullable=False, server_default='false'),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )

    # Create indexes for human_review_tickets
    op.create_index(
        'ix_human_review_tickets_status_priority',
        'human_review_tickets',
        ['status', 'priority']
    )
    op.create_index(
        'ix_human_review_tickets_assigned',
        'human_review_tickets',
        ['assigned_to', 'status']
    )
    op.create_index(
        'ix_human_review_tickets_due',
        'human_review_tickets',
        ['due_at', 'status']
    )

    # Create check constraints
    op.create_check_constraint(
        'ck_human_review_tickets_status',
        'human_review_tickets',
        "status IN ('pending', 'assigned', 'in_review', 'approved', 'rejected', 'escalated')"
    )
    op.create_check_constraint(
        'ck_human_review_tickets_risk_level',
        'human_review_tickets',
        "risk_level IN ('low', 'medium', 'high', 'critical')"
    )
    op.create_check_constraint(
        'ck_human_review_tickets_priority',
        'human_review_tickets',
        "priority IN ('low', 'normal', 'high', 'urgent')"
    )


def downgrade() -> None:
    # Drop tables in reverse order (due to foreign keys)
    op.drop_table('human_review_tickets')
    op.drop_table('workflow_state_logs')
    op.drop_table('compliance_audit_logs')
