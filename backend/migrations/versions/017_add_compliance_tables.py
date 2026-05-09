"""Add compliance execution tables

Revision ID: 017_compliance
Revises: 016_add_missing_chat_session_columns
Create Date: 2026-01-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '017_compliance'
down_revision = '016_chat_session_columns'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ===========================================
    # 1. Compliance Executions Table
    # ===========================================
    op.create_table(
        'compliance_executions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('tenant_id', UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('user_id', UUID(as_uuid=True), nullable=False),
        sa.Column('template_id', sa.String(100), nullable=False),
        sa.Column('template_version', sa.String(20), nullable=False),
        sa.Column('template_name', sa.String(255), nullable=True),

        # Subject being checked
        sa.Column('subject_id', sa.String(255), nullable=False),
        sa.Column('subject_type', sa.String(50), nullable=False),  # 'customer', 'transaction', 'entity'
        sa.Column('subject_name', sa.String(255), nullable=True),

        # Execution status
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),

        # Results
        sa.Column('risk_score', sa.Numeric(5, 4), nullable=True),
        sa.Column('risk_level', sa.String(20), nullable=True),  # 'low', 'medium', 'high', 'critical'
        sa.Column('recommendation', sa.String(50), nullable=True),  # 'approve', 'review', 'escalate', 'reject'
        sa.Column('results', JSONB, nullable=False, server_default='{}'),
        sa.Column('execution_trace', JSONB, nullable=False, server_default='[]'),

        # Timing
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('elapsed_ms', sa.Integer, nullable=True),

        # Review fields
        sa.Column('reviewed_by', UUID(as_uuid=True), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('review_decision', sa.String(50), nullable=True),
        sa.Column('review_notes', sa.Text, nullable=True),

        # Audit
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),

        # Foreign keys
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'],
                                name='fk_compliance_executions_tenant', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'],
                                name='fk_compliance_executions_user', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'],
                                name='fk_compliance_executions_reviewer', ondelete='SET NULL'),

        # Check constraints
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'manual_review')",
            name='ck_compliance_executions_status'
        ),
        sa.CheckConstraint(
            "risk_level IS NULL OR risk_level IN ('low', 'medium', 'high', 'critical')",
            name='ck_compliance_executions_risk_level'
        ),
    )

    # Indexes for compliance_executions
    op.create_index('ix_compliance_executions_subject',
                    'compliance_executions', ['subject_id', 'subject_type'])
    op.create_index('ix_compliance_executions_status',
                    'compliance_executions', ['status'])
    op.create_index('ix_compliance_executions_risk',
                    'compliance_executions', ['risk_level', 'risk_score'])
    op.create_index('ix_compliance_executions_created',
                    'compliance_executions', ['created_at'])
    op.create_index('ix_compliance_executions_template',
                    'compliance_executions', ['template_id'])

    # ===========================================
    # 2. Compliance Screening Results Table
    # ===========================================
    op.create_table(
        'compliance_screening_results',
        sa.Column('id', UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('execution_id', UUID(as_uuid=True), nullable=False),
        sa.Column('screening_type', sa.String(50), nullable=False),  # 'sanctions', 'pep', 'adverse_media'
        sa.Column('database_checked', sa.String(100), nullable=True),
        sa.Column('query_params', JSONB, nullable=True),
        sa.Column('results', JSONB, nullable=False, server_default='{}'),
        sa.Column('match_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('highest_match_score', sa.Numeric(5, 4), nullable=True),
        sa.Column('flagged', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),

        # Foreign keys
        sa.ForeignKeyConstraint(['execution_id'], ['compliance_executions.id'],
                                name='fk_screening_results_execution', ondelete='CASCADE'),

        # Check constraint
        sa.CheckConstraint(
            "screening_type IN ('sanctions', 'pep', 'adverse_media', 'jurisdiction')",
            name='ck_screening_results_type'
        ),
    )

    # Indexes
    op.create_index('ix_screening_results_execution',
                    'compliance_screening_results', ['execution_id'])
    op.create_index('ix_screening_results_type',
                    'compliance_screening_results', ['screening_type'])
    op.create_index('ix_screening_results_flagged',
                    'compliance_screening_results', ['flagged'])

    # ===========================================
    # 3. Enable RLS
    # ===========================================
    for table in ['compliance_executions']:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

        # SELECT policy
        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            FOR SELECT
            TO oniva_app
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)

        # INSERT policy
        op.execute(f"""
            CREATE POLICY tenant_insert_{table} ON {table}
            FOR INSERT
            TO oniva_app
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)

        # UPDATE policy
        op.execute(f"""
            CREATE POLICY tenant_update_{table} ON {table}
            FOR UPDATE
            TO oniva_app
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)

        # DELETE policy
        op.execute(f"""
            CREATE POLICY tenant_delete_{table} ON {table}
            FOR DELETE
            TO oniva_app
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
        """)

    # RLS for screening_results (through execution_id join)
    op.execute("""
        ALTER TABLE compliance_screening_results ENABLE ROW LEVEL SECURITY;
        ALTER TABLE compliance_screening_results FORCE ROW LEVEL SECURITY;

        CREATE POLICY tenant_isolation_screening_results ON compliance_screening_results
        FOR ALL
        TO oniva_app
        USING (
            execution_id IN (
                SELECT id FROM compliance_executions
                WHERE tenant_id = current_setting('app.current_tenant', true)::uuid
            )
        );
    """)


def downgrade() -> None:
    # Drop policies
    op.execute("DROP POLICY IF EXISTS tenant_isolation_screening_results ON compliance_screening_results")

    for table in ['compliance_executions']:
        op.execute(f"DROP POLICY IF EXISTS tenant_delete_{table} ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_update_{table} ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_insert_{table} ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")

    # Drop indexes
    op.drop_index('ix_screening_results_flagged')
    op.drop_index('ix_screening_results_type')
    op.drop_index('ix_screening_results_execution')
    op.drop_index('ix_compliance_executions_template')
    op.drop_index('ix_compliance_executions_created')
    op.drop_index('ix_compliance_executions_risk')
    op.drop_index('ix_compliance_executions_status')
    op.drop_index('ix_compliance_executions_subject')

    # Drop tables
    op.drop_table('compliance_screening_results')
    op.drop_table('compliance_executions')
