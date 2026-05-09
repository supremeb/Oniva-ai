"""Add cost analytics materialized view

Enterprise feature: Pre-aggregated cost data for fast dashboard queries.

Aggregates LLM usage costs hourly by tenant, user, workflow, provider, and model
for efficient cost attribution and reporting.

Revision ID: 060_cost_analytics_view
Revises: 059_enable_rls_documents
Create Date: 2026-01-31
"""
from alembic import op

revision = '060_cost_analytics_view'
down_revision = '059_enable_rls_documents'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create materialized view for hourly cost aggregation
    op.execute("""
        CREATE MATERIALIZED VIEW usage_costs_hourly AS
        SELECT
            tenant_id,
            user_id,
            workflow_id,
            provider_name,
            model,
            date_trunc('hour', created_at) as hour_bucket,
            SUM(cost_usd) as total_cost_usd,
            SUM(input_tokens) as total_input_tokens,
            SUM(output_tokens) as total_output_tokens,
            SUM(input_tokens + output_tokens) as total_tokens,
            COUNT(*) as api_call_count,
            AVG(latency_ms) as avg_latency_ms,
            MAX(latency_ms) as max_latency_ms
        FROM llm_usage_records
        GROUP BY tenant_id, user_id, workflow_id, provider_name, model, hour_bucket;
    """)

    # Create indexes on materialized view for fast queries
    op.execute("""
        CREATE UNIQUE INDEX idx_usage_costs_hourly_composite
        ON usage_costs_hourly(tenant_id, user_id, workflow_id, provider_name, model, hour_bucket);
    """)

    op.execute("""
        CREATE INDEX idx_usage_costs_hourly_tenant_hour
        ON usage_costs_hourly(tenant_id, hour_bucket);
    """)

    op.execute("""
        CREATE INDEX idx_usage_costs_hourly_workflow
        ON usage_costs_hourly(workflow_id, hour_bucket)
        WHERE workflow_id IS NOT NULL;
    """)

    print("✅ Cost analytics materialized view created")
    print("ℹ️  Refresh view with: REFRESH MATERIALIZED VIEW CONCURRENTLY usage_costs_hourly;")
    print("ℹ️  Set up hourly cron job for automatic refresh")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS usage_costs_hourly CASCADE")
