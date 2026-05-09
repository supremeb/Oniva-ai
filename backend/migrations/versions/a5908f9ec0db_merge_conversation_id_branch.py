"""merge_conversation_id_branch

Revision ID: a5908f9ec0db
Revises: 068_add_conversation_id_to_background_jobs, 072_add_gin_index_for_bm25
Create Date: 2026-02-17 17:11:17.023991

"""


# revision identifiers, used by Alembic.
revision = 'a5908f9ec0db'
down_revision = ('068_add_conversation_id_to_background_jobs', '072_add_gin_index_for_bm25')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
