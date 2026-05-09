"""add routing_policies table

Revision ID: b5_001_routing
Revises: 93e27b222f4b
Create Date: 2026-03-07

"""

# revision identifiers, used by Alembic.
revision = "b5_001_routing"
down_revision = "93e27b222f4b"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def upgrade() -> None:
    op.create_table(
        "routing_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("output_type", sa.String(50), nullable=True),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_tokens", sa.Integer, nullable=True),
        sa.Column("temperature", sa.Float, nullable=True),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Enable RLS
    op.execute("ALTER TABLE routing_policies ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY routing_policies_tenant_isolation ON routing_policies "
        "USING (tenant_id = current_setting('app.current_tenant', true)::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS routing_policies_tenant_isolation ON routing_policies")
    op.drop_table("routing_policies")
