"""Fix workflow checkpoint integrity - add FK constraint and cleanup orphans.

Revision ID: 062_fix_checkpoint_integrity
Revises: 061_webhook_triggers
Create Date: 2026-01-31

This migration:
1. Cleans up orphaned workflow_checkpoints (where workflow_uuid doesn't exist in workflows)
2. Adds a foreign key constraint on workflow_checkpoints.workflow_uuid to prevent future orphans
3. Adds an index on workflow_uuid for performance
"""
from alembic import op

# revision identifiers, used by Alembic
revision = '062_fix_checkpoint_integrity'
down_revision = '061_webhook_triggers'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Clean up orphaned checkpoints and add foreign key constraint.
    """
    # Step 1: Backup orphaned checkpoints before deletion
    op.execute("""
        CREATE TABLE IF NOT EXISTS _backup_orphaned_checkpoints_062 AS
        SELECT * FROM workflow_checkpoints
        WHERE workflow_uuid NOT IN (
            SELECT id FROM workflows
        )
    """)
    op.execute("""
        COMMENT ON TABLE _backup_orphaned_checkpoints_062 IS
        'Backup of orphaned checkpoints before FK constraint added in migration 062. Safe to drop after 2026-08-06.'
    """)

    # Step 2: Delete orphaned checkpoints (now safely backed up)
    op.execute("""
        DELETE FROM workflow_checkpoints
        WHERE workflow_uuid NOT IN (
            SELECT id FROM workflows
        )
    """)

    # Step 3: Add index on workflow_uuid for better query performance
    # Check if index exists first to make migration idempotent
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_workflow_checkpoints_workflow_uuid
        ON workflow_checkpoints (workflow_uuid)
    """)

    # Step 4: Add foreign key constraint to prevent future orphans
    # Using CASCADE so deleting a workflow also deletes its checkpoints
    op.create_foreign_key(
        'fk_workflow_checkpoints_workflow_uuid',
        'workflow_checkpoints',
        'workflows',
        ['workflow_uuid'],
        ['id'],
        ondelete='CASCADE'
    )

    # Log the cleanup
    print("Cleaned up orphaned workflow checkpoints and added foreign key constraint")


def downgrade() -> None:
    """
    Remove the foreign key constraint and restore backed-up checkpoints.
    """
    # Remove the foreign key constraint
    op.drop_constraint(
        'fk_workflow_checkpoints_workflow_uuid',
        'workflow_checkpoints',
        type_='foreignkey'
    )

    # Restore orphaned checkpoints from backup if the table exists
    op.execute("""
        INSERT INTO workflow_checkpoints
        SELECT * FROM _backup_orphaned_checkpoints_062
        ON CONFLICT DO NOTHING
    """)

    # Optionally remove the index (uncomment if needed)
    # op.drop_index('ix_workflow_checkpoints_workflow_uuid', table_name='workflow_checkpoints')
