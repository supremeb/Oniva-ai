"""Add projects workspace tables

Revision ID: 077_add_projects
Revises: 076_add_chat_action_type
Create Date: 2026-03-02

Changes:
- Create projects table with RLS policy
- Create project_members table with RLS policy
- Add project_id FK to chat_sessions
- Add source_type column to knowledge_bases
- Relax knowledge_bases unique constraint to include source_type
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '077_add_projects'
down_revision = '076_add_chat_action_type'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add source_type to knowledge_bases
    op.add_column(
        'knowledge_bases',
        sa.Column('source_type', sa.String(20), nullable=False, server_default='user'),
    )

    # Relax uniqueness: (tenant_id, name) -> (tenant_id, name, source_type)
    op.drop_constraint('uq_knowledge_base_name', 'knowledge_bases', type_='unique')
    op.create_unique_constraint(
        'uq_knowledge_base_name',
        'knowledge_bases',
        ['tenant_id', 'name', 'source_type'],
    )

    # 2. Create projects table
    op.create_table(
        'projects',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('owner_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('instructions', sa.Text, nullable=True),
        sa.Column('default_model', sa.String(255), nullable=True),
        sa.Column('knowledge_base_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('knowledge_bases.id', ondelete='RESTRICT'), nullable=True),
        sa.Column('settings', postgresql.JSONB, nullable=False, server_default='{}'),
        sa.Column('is_archived', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('row_version', sa.Integer, nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint('length(instructions) <= 10000', name='ck_projects_instructions_length'),
    )
    op.create_index('idx_projects_tenant_id', 'projects', ['tenant_id'])
    op.create_index('idx_projects_owner_id', 'projects', ['owner_id'])

    # RLS for projects
    op.execute("ALTER TABLE projects ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY projects_tenant_isolation ON projects
            USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
    """)

    # 3. Create project_members table
    op.create_table(
        'project_members',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('project_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(20), nullable=False, server_default='viewer'),
        sa.Column('invited_by', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('tenant_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('project_id', 'user_id', name='uq_project_member'),
        sa.CheckConstraint("role IN ('editor', 'viewer')", name='ck_project_member_role'),
    )
    op.create_index('idx_project_members_project_id', 'project_members', ['project_id'])
    op.create_index('idx_project_members_user_id', 'project_members', ['user_id'])
    op.create_index('idx_project_members_tenant_id', 'project_members', ['tenant_id'])

    # RLS for project_members
    op.execute("ALTER TABLE project_members ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY project_members_tenant_isolation ON project_members
            USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
    """)

    # 4. Add project_id to chat_sessions
    op.add_column(
        'chat_sessions',
        sa.Column('project_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('projects.id', ondelete='SET NULL'), nullable=True),
    )
    op.create_index('idx_chat_sessions_project_id', 'chat_sessions', ['project_id'])


def downgrade() -> None:
    # Remove project_id from chat_sessions
    op.drop_index('idx_chat_sessions_project_id', table_name='chat_sessions')
    op.drop_column('chat_sessions', 'project_id')

    # Drop project_members
    op.execute("DROP POLICY IF EXISTS project_members_tenant_isolation ON project_members")
    op.drop_table('project_members')

    # Drop projects
    op.execute("DROP POLICY IF EXISTS projects_tenant_isolation ON projects")
    op.drop_table('projects')

    # Restore knowledge_bases constraint
    op.drop_constraint('uq_knowledge_base_name', 'knowledge_bases', type_='unique')
    op.create_unique_constraint(
        'uq_knowledge_base_name',
        'knowledge_bases',
        ['tenant_id', 'name'],
    )
    op.drop_column('knowledge_bases', 'source_type')
