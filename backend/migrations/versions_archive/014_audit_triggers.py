"""Create audit triggers on key tables

Revision ID: 014_audit_triggers
Revises: 013_audit_events
Create Date: 2026-01-07
"""
from alembic import op

revision = '014_audit_triggers'
down_revision = '013_audit_events'
branch_labels = None
depends_on = None

# Tables to audit with their name column
AUDITED_TABLES = [
    ('workflows', 'name'),
    ('custom_agents', 'name'),
    ('connectors', 'name'),
    ('api_keys', 'name'),
]


def upgrade() -> None:
    # Create generic audit trigger function
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_trigger_func()
        RETURNS TRIGGER AS $$
        DECLARE
            v_action TEXT;
            v_changes JSONB;
            v_resource_name TEXT;
            v_user_id UUID;
            v_user_email TEXT;
            v_tenant_id UUID;
        BEGIN
            -- Determine action
            IF TG_OP = 'INSERT' THEN
                v_action := 'CREATE';
                v_changes := jsonb_build_object('after', row_to_json(NEW));
                v_resource_name := NEW.name;
                v_tenant_id := NEW.tenant_id;
            ELSIF TG_OP = 'UPDATE' THEN
                v_action := 'UPDATE';
                v_changes := jsonb_build_object(
                    'before', row_to_json(OLD),
                    'after', row_to_json(NEW)
                );
                v_resource_name := NEW.name;
                v_tenant_id := NEW.tenant_id;
            ELSIF TG_OP = 'DELETE' THEN
                v_action := 'DELETE';
                v_changes := jsonb_build_object('before', row_to_json(OLD));
                v_resource_name := OLD.name;
                v_tenant_id := OLD.tenant_id;
            END IF;

            -- Get user info from session variable (set by middleware)
            v_user_id := NULLIF(current_setting('app.current_user_id', true), '')::UUID;
            v_user_email := current_setting('app.current_user_email', true);

            -- Insert audit event (only if tenant_id exists)
            IF v_tenant_id IS NOT NULL THEN
                INSERT INTO audit_events (
                    tenant_id,
                    user_id,
                    user_email,
                    action,
                    resource_type,
                    resource_id,
                    resource_name,
                    changes,
                    ip_address,
                    user_agent
                ) VALUES (
                    v_tenant_id,
                    v_user_id,
                    v_user_email,
                    v_action,
                    TG_TABLE_NAME,
                    COALESCE(NEW.id, OLD.id),
                    v_resource_name,
                    v_changes,
                    NULLIF(current_setting('app.client_ip', true), '')::INET,
                    current_setting('app.user_agent', true)
                );
            END IF;

            -- Return appropriate row
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            ELSE
                RETURN NEW;
            END IF;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
    """)

    # Create triggers on each audited table
    for table, name_col in AUDITED_TABLES:
        op.execute(f"""
            CREATE TRIGGER audit_{table}
            AFTER INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func()
        """)

    # Special trigger for workflow executions (different structure)
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_workflow_execution()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Only audit if tenant_id exists
            IF NEW.tenant_id IS NOT NULL THEN
                INSERT INTO audit_events (
                    tenant_id,
                    user_id,
                    action,
                    resource_type,
                    resource_id,
                    resource_name,
                    changes,
                    metadata
                ) VALUES (
                    NEW.tenant_id,
                    NEW.user_id,
                    'EXECUTE',
                    'workflow_execution',
                    NEW.id,
                    (SELECT name FROM workflows WHERE id = NEW.workflow_id),
                    jsonb_build_object(
                        'status', NEW.status,
                        'workflow_id', NEW.workflow_id
                    ),
                    jsonb_build_object(
                        'started_at', NEW.started_at,
                        'completed_at', NEW.completed_at
                    )
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;

        CREATE TRIGGER audit_workflow_executions
        AFTER INSERT OR UPDATE ON workflow_executions
        FOR EACH ROW
        EXECUTE FUNCTION audit_workflow_execution();
    """)


def downgrade() -> None:
    # Drop triggers
    for table, _ in AUDITED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS audit_{table} ON {table}")

    op.execute("DROP TRIGGER IF EXISTS audit_workflow_executions ON workflow_executions")
    op.execute("DROP FUNCTION IF EXISTS audit_workflow_execution()")
    op.execute("DROP FUNCTION IF EXISTS audit_trigger_func()")
