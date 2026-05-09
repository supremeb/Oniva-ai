"""Merge skills branch into main migration chain

Revision ID: 025_merge_skills_branch
Revises: 024_add_compliance_audit_tables, 93e27b222f4b
Create Date: 2026-01-13

This is a merge-only migration that brings the orphan skills branch
(7262eef56aaf -> 93e27b222f4b) back into the main migration chain.

Skills branch history:
- 7262eef56aaf: Added skills table with base columns
- 93e27b222f4b: Added requires and mcp_dependencies JSON columns

No schema changes are made in this migration - it only resolves
the branching issue in the Alembic migration chain.
"""


# revision identifiers, used by Alembic.
revision = '025_merge_skills_branch'
down_revision = ('024_add_compliance_audit_tables', '93e27b222f4b')
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    No schema changes needed - this is a merge-only migration.

    The skills table and its dependencies column were already created
    by migrations 7262eef56aaf and 93e27b222f4b. This migration simply
    brings them into the main chain for proper dependency tracking.
    """
    pass


def downgrade() -> None:
    """
    No schema changes to revert - this was a merge-only migration.
    """
    pass
