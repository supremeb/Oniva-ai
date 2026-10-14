"""Alembic environment for database migrations"""

from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.models import Base
from app.config import get_settings

# Get configuration from alembic.ini
config = context.config

# Configure SQLAlchemy database URL
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("+asyncpg", ""))

# Setup logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set target metadata
target_metadata = Base.metadata


def _ensure_wide_version_table(connection) -> None:
    """Create/widen alembic_version.version_num to varchar(255) (long revision ids)."""
    from sqlalchemy import text

    if connection.dialect.name != "postgresql":
        return
    connection.execute(text(
        "CREATE TABLE IF NOT EXISTS alembic_version "
        "(version_num varchar(255) NOT NULL PRIMARY KEY)"
    ))
    connection.execute(text(
        "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE varchar(255)"
    ))
    connection.commit()


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode"""
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url")

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        _ensure_wide_version_table(connection)
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
