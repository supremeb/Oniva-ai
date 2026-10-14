"""Add missing columns to chat_sessions table

Revision ID: 016_chat_session_columns
Revises: 015_chat_panels
Create Date: 2026-01-08
"""
from alembic import op

revision = '016_chat_session_columns'
down_revision = '015_chat_panels'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add missing columns to chat_sessions table
    # These columns may already exist from the updated 015 migration for fresh installs

    # Try to add context_type column
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'chat_sessions' AND column_name = 'context_type'
            ) THEN
                ALTER TABLE chat_sessions ADD COLUMN context_type VARCHAR(20) NOT NULL DEFAULT 'agent';
            END IF;
        END $$;
    """)

    # Try to add is_pinned column
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'chat_sessions' AND column_name = 'is_pinned'
            ) THEN
                ALTER TABLE chat_sessions ADD COLUMN is_pinned BOOLEAN NOT NULL DEFAULT false;
            END IF;
        END $$;
    """)

    # Try to add message_count column
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'chat_sessions' AND column_name = 'message_count'
            ) THEN
                ALTER TABLE chat_sessions ADD COLUMN message_count INTEGER NOT NULL DEFAULT 0;
            END IF;
        END $$;
    """)

    # Try to add last_message_at column
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'chat_sessions' AND column_name = 'last_message_at'
            ) THEN
                ALTER TABLE chat_sessions ADD COLUMN last_message_at TIMESTAMP WITH TIME ZONE;
            END IF;
        END $$;
    """)

    # Rename metadata to session_metadata if it exists
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'chat_sessions' AND column_name = 'metadata'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'chat_sessions' AND column_name = 'session_metadata'
            ) THEN
                ALTER TABLE chat_sessions RENAME COLUMN metadata TO session_metadata;
            END IF;
        END $$;
    """)

    # Add session_metadata if neither exists
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'chat_sessions' AND column_name = 'session_metadata'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'chat_sessions' AND column_name = 'metadata'
            ) THEN
                ALTER TABLE chat_sessions ADD COLUMN session_metadata JSONB NOT NULL DEFAULT '{}';
            END IF;
        END $$;
    """)

    # Update existing records: set message_count from messages array length
    op.execute("""
        UPDATE chat_sessions
        SET message_count = COALESCE(jsonb_array_length(messages), 0)
        WHERE message_count = 0 AND messages IS NOT NULL AND jsonb_array_length(messages) > 0;
    """)

    # Update existing records: set context_type based on agent_id/workflow_id
    op.execute("""
        UPDATE chat_sessions
        SET context_type = CASE
            WHEN workflow_id IS NOT NULL THEN 'workflow'
            ELSE 'agent'
        END
        WHERE context_type = 'agent' AND workflow_id IS NOT NULL;
    """)

    # Create index on context_type for filtering
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE indexname = 'ix_chat_sessions_context_type'
            ) THEN
                CREATE INDEX ix_chat_sessions_context_type ON chat_sessions(context_type);
            END IF;
        END $$;
    """)

    # Create index on is_pinned for filtering pinned sessions
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE indexname = 'ix_chat_sessions_is_pinned'
            ) THEN
                CREATE INDEX ix_chat_sessions_is_pinned ON chat_sessions(is_pinned) WHERE is_pinned = true;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # Drop new indexes
    op.execute("DROP INDEX IF EXISTS ix_chat_sessions_is_pinned")
    op.execute("DROP INDEX IF EXISTS ix_chat_sessions_context_type")

    # Remove new columns
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS last_message_at")
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS message_count")
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS is_pinned")
    op.execute("ALTER TABLE chat_sessions DROP COLUMN IF EXISTS context_type")

    # Rename session_metadata back to metadata
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'chat_sessions' AND column_name = 'session_metadata'
            ) THEN
                ALTER TABLE chat_sessions RENAME COLUMN session_metadata TO metadata;
            END IF;
        END $$;
    """)
