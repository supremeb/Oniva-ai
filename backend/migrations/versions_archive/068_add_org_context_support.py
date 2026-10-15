"""Add organization context support to episodic memories.

- Make user_id nullable (shared memories survive user deletion)
- Change user_id FK from CASCADE to SET NULL
- Add team_id nullable FK (future team-scoped visibility)
- Update visibility check constraint: remove 'public', allow only 'private'/'shared'
- Migrate existing 'public' rows to 'private'
- Add partial HNSW index for shared memory vector search
- Add index on team_id where not null

Revision ID: 068_add_org_context_support
Revises: 067_add_cache_tracking_fields
Create Date: 2026-02-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = '068_add_org_context_support'
down_revision = '067_add_cache_tracking_fields'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Migrate existing 'public' rows to 'private' before constraint change
    op.execute("UPDATE episodic_memories SET visibility = 'private' WHERE visibility = 'public'")

    # 2. Drop old visibility check constraint if it exists
    op.execute("ALTER TABLE episodic_memories DROP CONSTRAINT IF EXISTS ck_episodic_memories_visibility")

    # 3. Add new visibility check constraint (only private/shared)
    op.create_check_constraint(
        'ck_episodic_memories_visibility',
        'episodic_memories',
        "visibility IN ('private', 'shared')"
    )

    # 4. Make user_id nullable (shared memories survive user deletion)
    op.alter_column(
        'episodic_memories',
        'user_id',
        existing_type=PGUUID(as_uuid=True),
        nullable=True,
    )

    # 5. Drop and recreate user_id FK with SET NULL (instead of CASCADE)
    # First find and drop the existing FK constraint
    op.execute("""
        DO $$ DECLARE
            fk_name TEXT;
        BEGIN
            SELECT conname INTO fk_name
            FROM pg_constraint
            WHERE conrelid = 'episodic_memories'::regclass
            AND contype = 'f'
            AND EXISTS (
                SELECT 1 FROM unnest(conkey) AS k
                JOIN pg_attribute a ON a.attrelid = conrelid AND a.attnum = k
                WHERE a.attname = 'user_id'
            )
            LIMIT 1;

            IF fk_name IS NOT NULL THEN
                EXECUTE 'ALTER TABLE episodic_memories DROP CONSTRAINT ' || fk_name;
            END IF;
        END $$;
    """)

    # Recreate with SET NULL
    op.create_foreign_key(
        'fk_episodic_memories_user_id',
        'episodic_memories',
        'users',
        ['user_id'],
        ['id'],
        ondelete='SET NULL',
    )

    # 6. Add team_id column (nullable, for future team-scoped visibility)
    op.add_column(
        'episodic_memories',
        sa.Column('team_id', PGUUID(as_uuid=True), sa.ForeignKey('teams.id', ondelete='SET NULL'), nullable=True)
    )

    # 7. Partial index on team_id where not null
    op.create_index(
        'ix_episodic_memories_team_id',
        'episodic_memories',
        ['team_id'],
        postgresql_where=sa.text("team_id IS NOT NULL"),
    )

    # 8. Partial HNSW index for shared memory vector search
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_episodic_memories_shared_embedding
        ON episodic_memories USING hnsw (embedding vector_cosine_ops)
        WHERE visibility = 'shared' AND archived = false
    """)

    # 9. Composite index for shared memory queries (tenant + visibility + archived)
    op.create_index(
        'ix_episodic_memories_tenant_shared',
        'episodic_memories',
        ['tenant_id', 'visibility'],
        postgresql_where=sa.text("visibility = 'shared' AND archived = false"),
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_episodic_memories_tenant_shared', table_name='episodic_memories')
    op.execute("DROP INDEX IF EXISTS ix_episodic_memories_shared_embedding")
    op.drop_index('ix_episodic_memories_team_id', table_name='episodic_memories')

    # Drop team_id column
    op.drop_column('episodic_memories', 'team_id')

    # Restore user_id FK to CASCADE
    op.drop_constraint('fk_episodic_memories_user_id', 'episodic_memories', type_='foreignkey')
    op.create_foreign_key(
        'fk_episodic_memories_user_id',
        'episodic_memories',
        'users',
        ['user_id'],
        ['id'],
        ondelete='CASCADE',
    )

    # Make user_id non-nullable again
    op.alter_column(
        'episodic_memories',
        'user_id',
        existing_type=PGUUID(as_uuid=True),
        nullable=False,
    )

    # Restore original visibility constraint (allow public)
    op.execute("ALTER TABLE episodic_memories DROP CONSTRAINT IF EXISTS ck_episodic_memories_visibility")
