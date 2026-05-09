"""merge routing policy migration

Revision ID: 0959413d1b2f
Revises: 077_add_projects, b5_001_routing
Create Date: 2026-03-07 18:43:53.150572

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0959413d1b2f'
down_revision = ('077_add_projects', 'b5_001_routing')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
