"""Phase A: Enterprise Agent Studio Enhancement

Adds:
- Teams and TeamMembership for RBAC (Phase A.2)
- ApprovalRequest for approval workflows (Phase A.3)
- Optimistic locking (row_version) on CustomAgent and AgentChatSession (Phase A.1)
- Risk tier and approval settings on CustomAgent

Revision ID: 063_phase_a_agent_studio
Revises: 062_fix_workflow_checkpoint_integrity
Create Date: 2026-02-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ENUM

revision = '063_phase_a_agent_studio'
down_revision = '062_fix_checkpoint_integrity'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types
    team_role_enum = ENUM('viewer', 'member', 'admin', 'owner', name='teamrole', create_type=False)
    approval_status_enum = ENUM('pending', 'approved', 'rejected', 'expired', name='approvalstatus', create_type=False)
    approval_request_type_enum = ENUM('deploy', 'force_deploy', 'delete', 'config_change', name='approvalrequesttype', create_type=False)

    # Create enum types in database
    op.execute("CREATE TYPE teamrole AS ENUM ('viewer', 'member', 'admin', 'owner')")
    op.execute("CREATE TYPE approvalstatus AS ENUM ('pending', 'approved', 'rejected', 'expired')")
    op.execute("CREATE TYPE approvalrequesttype AS ENUM ('deploy', 'force_deploy', 'delete', 'config_change')")

    # ========================================================================
    # Teams table (Phase A.2: RBAC)
    # ========================================================================
    op.create_table(
        'teams',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.String(1000), nullable=True),
        sa.Column('slug', sa.String(100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    op.create_index('idx_teams_tenant', 'teams', ['tenant_id'])
    op.create_index('idx_teams_slug', 'teams', ['slug'])
    op.create_unique_constraint('uq_teams_tenant_slug', 'teams', ['tenant_id', 'slug'])

    # ========================================================================
    # Team Memberships table (Phase A.2: RBAC)
    # ========================================================================
    op.create_table(
        'team_memberships',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('team_id', UUID(as_uuid=True), sa.ForeignKey('teams.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', team_role_enum, nullable=False, server_default='member'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    op.create_index('idx_team_memberships_team', 'team_memberships', ['team_id'])
    op.create_index('idx_team_memberships_user', 'team_memberships', ['user_id'])
    op.create_unique_constraint('uq_team_memberships_team_user', 'team_memberships', ['team_id', 'user_id'])

    # ========================================================================
    # Approval Requests table (Phase A.3: Approval Workflows)
    # ========================================================================
    op.create_table(
        'approval_requests',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('agent_id', UUID(as_uuid=True), sa.ForeignKey('custom_agents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('agent_version_id', UUID(as_uuid=True), sa.ForeignKey('agent_versions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('request_type', approval_request_type_enum, nullable=False),
        sa.Column('requester_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', approval_status_enum, nullable=False, server_default='pending'),
        sa.Column('reviewer_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('reason', sa.Text, nullable=True),
        sa.Column('review_comment', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index('idx_approval_requests_tenant', 'approval_requests', ['tenant_id'])
    op.create_index('idx_approval_requests_agent', 'approval_requests', ['agent_id'])
    op.create_index('idx_approval_requests_status', 'approval_requests', ['status'])

    # ========================================================================
    # Add columns to custom_agents (Phase A.2, A.3)
    # ========================================================================
    op.add_column('custom_agents', sa.Column('team_id', UUID(as_uuid=True), nullable=True))
    op.add_column('custom_agents', sa.Column('risk_tier', sa.String(20), server_default='low', nullable=True))
    op.add_column('custom_agents', sa.Column('requires_approval', sa.Boolean, server_default='false', nullable=True))
    op.add_column('custom_agents', sa.Column('row_version', sa.Integer, server_default='1', nullable=False))

    # Add foreign key for team_id
    op.create_foreign_key(
        'fk_custom_agents_team',
        'custom_agents', 'teams',
        ['team_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_index('idx_custom_agents_team', 'custom_agents', ['team_id'])

    # ========================================================================
    # Add row_version to agent_chat_sessions (Phase A.1)
    # ========================================================================
    op.add_column('agent_chat_sessions', sa.Column('row_version', sa.Integer, server_default='1', nullable=False))


def downgrade() -> None:
    # Remove row_version from agent_chat_sessions
    op.drop_column('agent_chat_sessions', 'row_version')

    # Remove columns and FK from custom_agents
    op.drop_index('idx_custom_agents_team', table_name='custom_agents')
    op.drop_constraint('fk_custom_agents_team', 'custom_agents', type_='foreignkey')
    op.drop_column('custom_agents', 'row_version')
    op.drop_column('custom_agents', 'requires_approval')
    op.drop_column('custom_agents', 'risk_tier')
    op.drop_column('custom_agents', 'team_id')

    # Drop approval_requests
    op.drop_index('idx_approval_requests_status', table_name='approval_requests')
    op.drop_index('idx_approval_requests_agent', table_name='approval_requests')
    op.drop_index('idx_approval_requests_tenant', table_name='approval_requests')
    op.drop_table('approval_requests')

    # Drop team_memberships
    op.drop_constraint('uq_team_memberships_team_user', 'team_memberships', type_='unique')
    op.drop_index('idx_team_memberships_user', table_name='team_memberships')
    op.drop_index('idx_team_memberships_team', table_name='team_memberships')
    op.drop_table('team_memberships')

    # Drop teams
    op.drop_constraint('uq_teams_tenant_slug', 'teams', type_='unique')
    op.drop_index('idx_teams_slug', table_name='teams')
    op.drop_index('idx_teams_tenant', table_name='teams')
    op.drop_table('teams')

    # Drop enum types
    op.execute("DROP TYPE approvalrequesttype")
    op.execute("DROP TYPE approvalstatus")
    op.execute("DROP TYPE teamrole")
