"""Fix missing ensure_single_active_version trigger

Revision ID: 053_fix_workflow_version_trigger
Revises: 052b_add_workflow_version_indexes
Create Date: 2026-01-30

This migration ensures the ensure_single_active_version trigger exists.
The trigger was defined in migration 035 but may not have been applied
in some environments. This migration is idempotent.
"""
from alembic import op

revision = '053_fix_workflow_version_trigger'
down_revision = '052b_add_workflow_version_indexes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create or replace the function (idempotent)
    op.execute("""
        CREATE OR REPLACE FUNCTION ensure_single_active_version()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.is_active = true AND (OLD IS NULL OR OLD.is_active = false) THEN
                -- Deactivate all other versions for this workflow
                UPDATE workflow_versions
                SET is_active = false
                WHERE workflow_id = NEW.workflow_id
                  AND id != NEW.id
                  AND is_active = true;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # Drop trigger if exists (to make this migration idempotent)
    op.execute("DROP TRIGGER IF EXISTS ensure_single_active_version_trigger ON workflow_versions;")

    # Create the trigger
    op.execute("""
        CREATE TRIGGER ensure_single_active_version_trigger
            BEFORE INSERT OR UPDATE OF is_active ON workflow_versions
            FOR EACH ROW
            WHEN (NEW.is_active = true)
            EXECUTE FUNCTION ensure_single_active_version();
    """)

    # Fix any existing duplicate active versions
    # Keep only the highest version_number as active for each workflow
    op.execute("""
        WITH latest_active AS (
            SELECT workflow_id, MAX(version_number) as max_version
            FROM workflow_versions
            WHERE is_active = true
            GROUP BY workflow_id
            HAVING COUNT(*) > 1
        )
        UPDATE workflow_versions wv
        SET is_active = false
        FROM latest_active la
        WHERE wv.workflow_id = la.workflow_id
          AND wv.is_active = true
          AND wv.version_number < la.max_version;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ensure_single_active_version_trigger ON workflow_versions;")
    op.execute("DROP FUNCTION IF EXISTS ensure_single_active_version();")
