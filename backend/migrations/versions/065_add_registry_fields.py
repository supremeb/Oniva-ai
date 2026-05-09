"""Add registry fields for Phase B agent discovery and sharing.

Adds to custom_agents:
- visibility (enum: private/team/tenant)
- category (string for filtering)
- published_at (timestamp)
- clone_count (integer)
- cloned_from_id (FK for lineage)
- compliance_tags (array with GIN index)
- compliance_notes (text)
- is_supervisor, child_agent_config, execution_mode, resource_constraints (orchestration)

Adds to compliance_audit_logs:
- trace_id (string for multi-agent correlation)

Revision ID: 065_add_registry_fields
Revises: 064_add_agent_learnings
Create Date: 2026-02-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY

revision = '065_add_registry_fields'
down_revision = '064_add_agent_learnings'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Create visibility enum
    agentvisibility = sa.Enum('private', 'team', 'tenant', name='agentvisibility')
    agentvisibility.create(op.get_bind(), checkfirst=True)

    # 2. Add registry columns to custom_agents
    op.add_column('custom_agents',
        sa.Column('visibility', agentvisibility, server_default='private', nullable=False))
    op.add_column('custom_agents',
        sa.Column('category', sa.String(50), nullable=True))
    op.add_column('custom_agents',
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('custom_agents',
        sa.Column('clone_count', sa.Integer, server_default='0', nullable=False))
    op.add_column('custom_agents',
        sa.Column('cloned_from_id', UUID(as_uuid=True), nullable=True))

    # 3. Compliance tags (industry-agnostic, GIN-indexed array)
    op.add_column('custom_agents',
        sa.Column('compliance_tags', ARRAY(sa.String(50)), server_default='{}', nullable=False))
    op.add_column('custom_agents',
        sa.Column('compliance_notes', sa.Text, nullable=True))

    # 4. Orchestration columns
    op.add_column('custom_agents',
        sa.Column('is_supervisor', sa.Boolean, server_default='false', nullable=False))
    op.add_column('custom_agents',
        sa.Column('child_agent_config', JSONB, server_default='[]', nullable=False))
    op.add_column('custom_agents',
        sa.Column('execution_mode', sa.String(20), server_default='sequential', nullable=False))
    op.add_column('custom_agents',
        sa.Column('resource_constraints', JSONB, server_default='{}', nullable=False))

    # 5. Indexes for organization tab queries
    op.create_index('ix_custom_agents_tenant_visibility',
                    'custom_agents', ['tenant_id', 'visibility'])
    op.create_index('ix_custom_agents_compliance_tags',
                    'custom_agents', ['compliance_tags'],
                    postgresql_using='gin')

    # 6. Add trace_id to existing ComplianceAuditLog for multi-agent correlation
    op.add_column('compliance_audit_logs',
        sa.Column('trace_id', sa.String(32), nullable=True))
    op.create_index('ix_compliance_audit_logs_trace_id',
                    'compliance_audit_logs', ['trace_id'])


def downgrade():
    # Remove trace_id from compliance_audit_logs
    op.drop_index('ix_compliance_audit_logs_trace_id', table_name='compliance_audit_logs')
    op.drop_column('compliance_audit_logs', 'trace_id')

    # Remove indexes
    op.drop_index('ix_custom_agents_compliance_tags', table_name='custom_agents')
    op.drop_index('ix_custom_agents_tenant_visibility', table_name='custom_agents')

    # Remove orchestration columns
    op.drop_column('custom_agents', 'resource_constraints')
    op.drop_column('custom_agents', 'execution_mode')
    op.drop_column('custom_agents', 'child_agent_config')
    op.drop_column('custom_agents', 'is_supervisor')

    # Remove compliance columns
    op.drop_column('custom_agents', 'compliance_notes')
    op.drop_column('custom_agents', 'compliance_tags')

    # Remove registry columns
    op.drop_column('custom_agents', 'cloned_from_id')
    op.drop_column('custom_agents', 'clone_count')
    op.drop_column('custom_agents', 'published_at')
    op.drop_column('custom_agents', 'category')
    op.drop_column('custom_agents', 'visibility')

    # Drop enum
    sa.Enum(name='agentvisibility').drop(op.get_bind(), checkfirst=True)
