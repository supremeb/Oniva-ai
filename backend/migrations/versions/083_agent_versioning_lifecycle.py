"""Agent versioning lifecycle: sequential versions, agent numbers, aliases

Revision ID: 083_agent_versioning_lifecycle
Revises: 082_create_run_events_table
Create Date: 2026-04-05

Adds:
- sequence_number to custom_agents (AGT-001 human-readable IDs)
- version_number, name, visibility, published_at, compliance_tags, created_by to agent_versions
- agent_aliases table with RLS for PRODUCTION/STAGING deployment pointers
- Backfills existing data (sequence_number, version_number, PRODUCTION aliases)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY

revision = '083_agent_versioning_lifecycle'
down_revision = '082_create_run_events_table'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===== 1. CustomAgent: Add sequence_number =====
    op.add_column('custom_agents',
        sa.Column('sequence_number', sa.Integer, nullable=True)
    )

    # ===== 2. AgentVersion: Make legacy 'version' column nullable =====
    op.alter_column('agent_versions', 'version', nullable=True)

    # ===== 2b. AgentVersion: Add new columns =====
    # Sequential integer version number
    op.add_column('agent_versions',
        sa.Column('version_number', sa.Integer, nullable=True)
    )
    # User-provided version label
    op.add_column('agent_versions',
        sa.Column('name', sa.Text, nullable=True)
    )
    # Publishing fields (moved from CustomAgent)
    op.add_column('agent_versions',
        sa.Column('visibility', sa.String(20), server_default='private', nullable=True)
    )
    op.add_column('agent_versions',
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('agent_versions',
        sa.Column('compliance_tags', ARRAY(sa.String(50)), nullable=True)
    )
    # Accuracy/deploy gate columns (migration 023 may not have run on this DB)
    op.add_column('agent_versions',
        sa.Column('accuracy', sa.Float, nullable=True)
    )
    op.add_column('agent_versions',
        sa.Column('accuracy_at_deploy', sa.Float, nullable=True)
    )
    op.add_column('agent_versions',
        sa.Column('test_count', sa.Integer, server_default='0')
    )
    op.add_column('agent_versions',
        sa.Column('deploy_blocked', sa.Boolean, server_default='false')
    )
    op.add_column('agent_versions',
        sa.Column('block_reason', sa.Text, nullable=True)
    )
    # Who created this version
    op.add_column('agent_versions',
        sa.Column('created_by', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    )

    # ===== 3. Create agent_aliases table =====
    op.create_table(
        'agent_aliases',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True),
                  sa.ForeignKey('tenants.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('agent_id', UUID(as_uuid=True),
                  sa.ForeignKey('custom_agents.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('alias_name', sa.String(50), nullable=False),
        sa.Column('version_id', UUID(as_uuid=True),
                  sa.ForeignKey('agent_versions.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('updated_by', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.UniqueConstraint('agent_id', 'alias_name', name='uq_agent_alias_name'),
    )

    # ===== 4. RLS on agent_aliases =====
    op.execute("ALTER TABLE agent_aliases ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON agent_aliases
        USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
    """)

    # ===== 5. Backfill sequence_number on custom_agents =====
    # Assign sequential numbers per tenant, ordered by created_at then id
    op.execute("""
        WITH numbered AS (
            SELECT id, tenant_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY tenant_id
                       ORDER BY created_at ASC, id ASC
                   ) AS seq
            FROM custom_agents
        )
        UPDATE custom_agents
        SET sequence_number = numbered.seq
        FROM numbered
        WHERE custom_agents.id = numbered.id
    """)

    # ===== 6. Backfill version_number on agent_versions =====
    # Assign sequential numbers per agent, ordered by created_at then id
    op.execute("""
        WITH numbered AS (
            SELECT id, agent_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY agent_id
                       ORDER BY created_at ASC, id ASC
                   ) AS seq
            FROM agent_versions
        )
        UPDATE agent_versions
        SET version_number = numbered.seq
        FROM numbered
        WHERE agent_versions.id = numbered.id
    """)

    # Copy legacy semver string to name field for existing versions
    op.execute("""
        UPDATE agent_versions
        SET name = 'v' || version_number::text
        WHERE name IS NULL AND version_number IS NOT NULL
    """)

    # ===== 7. Backfill PRODUCTION aliases for deployed agents =====
    op.execute("""
        INSERT INTO agent_aliases (id, tenant_id, agent_id, alias_name, version_id, updated_by)
        SELECT
            gen_random_uuid(),
            av.tenant_id,
            av.agent_id,
            'PRODUCTION',
            av.id,
            av.deployed_by
        FROM agent_versions av
        WHERE av.is_deployed = true
        ON CONFLICT (agent_id, alias_name) DO NOTHING
    """)

    # ===== 8. Add constraints now that data is backfilled =====
    # Unique constraint on (tenant_id, sequence_number) for custom_agents
    op.create_unique_constraint(
        'uq_custom_agent_tenant_sequence',
        'custom_agents',
        ['tenant_id', 'sequence_number']
    )

    # Unique constraint on (agent_id, version_number)
    op.create_unique_constraint(
        'uq_agent_version_number',
        'agent_versions',
        ['agent_id', 'version_number']
    )


def downgrade() -> None:
    # Drop constraints
    op.drop_constraint('uq_agent_version_number', 'agent_versions')
    op.drop_constraint('uq_custom_agent_tenant_sequence', 'custom_agents')

    # Drop RLS
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON agent_aliases")
    op.execute("ALTER TABLE agent_aliases DISABLE ROW LEVEL SECURITY")

    # Drop agent_aliases table
    op.drop_table('agent_aliases')

    # Remove new columns from agent_versions
    op.drop_column('agent_versions', 'created_by')
    op.drop_column('agent_versions', 'compliance_tags')
    op.drop_column('agent_versions', 'published_at')
    op.drop_column('agent_versions', 'visibility')
    op.drop_column('agent_versions', 'block_reason')
    op.drop_column('agent_versions', 'deploy_blocked')
    op.drop_column('agent_versions', 'test_count')
    op.drop_column('agent_versions', 'accuracy_at_deploy')
    op.drop_column('agent_versions', 'accuracy')
    op.drop_column('agent_versions', 'name')
    op.drop_column('agent_versions', 'version_number')

    # Remove sequence_number from custom_agents
    op.drop_column('custom_agents', 'sequence_number')
