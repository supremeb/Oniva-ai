"""Create agent_permissions and agent_feedback tables.

Phase D: Org Admin Dashboard - agent_permissions
Phase E: Inline Feedback - agent_feedback

Revision ID: 071_create_admin_and_feedback
Revises: 070_create_knowledge_tables
Create Date: 2026-02-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '071_create_admin_and_feedback'
down_revision = '070_create_knowledge_tables'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- agent_permissions (Phase D) ---
    op.create_table(
        'agent_permissions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('agent_id', UUID(as_uuid=True), sa.ForeignKey('custom_agents.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('permission_type', sa.String(50), nullable=False),
        sa.Column('team_id', UUID(as_uuid=True), sa.ForeignKey('teams.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('agent_id', 'permission_type', 'team_id', name='uq_agent_permission'),
    )

    op.execute("ALTER TABLE agent_permissions ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation_agent_permissions ON agent_permissions
            FOR ALL
            TO oniva_app
            USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
    """)

    # --- agent_feedback (Phase E) ---
    op.create_table(
        'agent_feedback',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('agent_id', UUID(as_uuid=True), sa.ForeignKey('custom_agents.id', ondelete='CASCADE'), nullable=True, index=True),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('session_id', sa.String(255), nullable=True),
        sa.Column('message_index', sa.Integer, nullable=True),
        sa.Column('rating', sa.String(10), nullable=False),
        sa.Column('feedback_text', sa.Text, nullable=True),
        sa.Column('agent_response_summary', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_agent_feedback_agent_created', 'agent_feedback', ['agent_id', 'created_at'])

    op.execute("ALTER TABLE agent_feedback ENABLE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation_agent_feedback ON agent_feedback
            FOR ALL
            TO oniva_app
            USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_agent_feedback ON agent_feedback;")
    op.drop_table('agent_feedback')

    op.execute("DROP POLICY IF EXISTS tenant_isolation_agent_permissions ON agent_permissions;")
    op.drop_table('agent_permissions')
