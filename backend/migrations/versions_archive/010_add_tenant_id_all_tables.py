"""Add tenant_id to all tables for multi-tenancy

Revision ID: 010_tenant_id_all
Revises: 009_users_tenant_id
Create Date: 2026-01-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '010_tenant_id_all'
down_revision = '009_users_tenant_id'
branch_labels = None
depends_on = None

# Tables that need tenant_id (all have user_id FK)
TABLES_WITH_USER_FK = [
    'api_keys',
    'connectors',
    'custom_agents',
    'workflows',
    'workflow_executions',
    'documents',
    'audit_logs',
]


def upgrade() -> None:
    # Get demo tenant ID for migration
    demo_tenant = '11111111-1111-1111-1111-111111111111'

    # Add tenant_id to each table
    for table in TABLES_WITH_USER_FK:
        # Add column (nullable first)
        op.add_column(table, sa.Column('tenant_id', UUID(as_uuid=True), nullable=True))

        # Populate from user's tenant_id
        op.execute(f"""
            UPDATE {table} t
            SET tenant_id = u.tenant_id
            FROM users u
            WHERE t.user_id = u.id
        """)

        # Set remaining rows to demo tenant (orphaned data)
        op.execute(f"""
            UPDATE {table}
            SET tenant_id = '{demo_tenant}'
            WHERE tenant_id IS NULL
        """)

        # Make NOT NULL
        op.alter_column(table, 'tenant_id', nullable=False)

        # Add foreign key
        op.create_foreign_key(
            f'fk_{table}_tenant_id',
            table, 'tenants',
            ['tenant_id'], ['id'],
            ondelete='CASCADE'
        )

        # Add index for tenant filtering
        op.create_index(f'ix_{table}_tenant_id', table, ['tenant_id'])

    # Handle skills table (uses author_id, not user_id)
    op.add_column('skills', sa.Column('tenant_id', UUID(as_uuid=True), nullable=True))

    # Custom skills get tenant from author, system skills stay NULL
    op.execute(f"""
        UPDATE skills s
        SET tenant_id = u.tenant_id
        FROM users u
        WHERE s.author_id = u.id AND s.is_custom = true
    """)

    # Add foreign key (allows NULL for system skills)
    op.create_foreign_key(
        'fk_skills_tenant_id',
        'skills', 'tenants',
        ['tenant_id'], ['id'],
        ondelete='CASCADE'
    )
    op.create_index('ix_skills_tenant_id', 'skills', ['tenant_id'])

    # Handle skill_executions (get tenant from skill or set demo tenant)
    op.add_column('skill_executions', sa.Column('tenant_id', UUID(as_uuid=True), nullable=True))

    # Try to get tenant from skills that have tenant_id
    op.execute(f"""
        UPDATE skill_executions se
        SET tenant_id = s.tenant_id
        FROM skills s
        WHERE se.skill_id = s.id AND s.tenant_id IS NOT NULL
    """)

    # Set remaining to demo tenant
    op.execute(f"""
        UPDATE skill_executions
        SET tenant_id = '{demo_tenant}'
        WHERE tenant_id IS NULL
    """)

    op.alter_column('skill_executions', 'tenant_id', nullable=False)
    op.create_foreign_key(
        'fk_skill_executions_tenant_id',
        'skill_executions', 'tenants',
        ['tenant_id'], ['id'],
        ondelete='CASCADE'
    )
    op.create_index('ix_skill_executions_tenant_id', 'skill_executions', ['tenant_id'])


def downgrade() -> None:
    # Remove from skill_executions
    op.drop_index('ix_skill_executions_tenant_id')
    op.drop_constraint('fk_skill_executions_tenant_id', 'skill_executions', type_='foreignkey')
    op.drop_column('skill_executions', 'tenant_id')

    # Remove from skills
    op.drop_index('ix_skills_tenant_id')
    op.drop_constraint('fk_skills_tenant_id', 'skills', type_='foreignkey')
    op.drop_column('skills', 'tenant_id')

    # Remove from all other tables
    for table in reversed(TABLES_WITH_USER_FK):
        op.drop_index(f'ix_{table}_tenant_id')
        op.drop_constraint(f'fk_{table}_tenant_id', table, type_='foreignkey')
        op.drop_column(table, 'tenant_id')
