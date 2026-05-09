"""Database connection management with tenant context support for RLS."""
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from uuid import UUID

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.config import get_settings
from app.core.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)

# Create async engine with production-ready pool settings
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,  # Disable query logging in production
    future=True,
    pool_pre_ping=True,
    pool_size=50,         # Increased from 10 for 500+ concurrent users
    max_overflow=100,     # Increased from 20 for burst capacity
    pool_timeout=30,      # Connection acquisition timeout
    pool_recycle=3600,    # Recycle connections after 1 hour
    connect_args={
        "server_settings": {
            "jit": "on",  # Enable JIT compilation in PostgreSQL
            "statement_timeout": "30000",  # 30s statement timeout
        },
        "command_timeout": 30,  # 30s command timeout
    },
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def set_tenant_context(session: AsyncSession, tenant_id: str) -> None:
    """
    Set tenant context on a database session using parameterized SQL.

    Uses set_config() with bind parameters to prevent SQL injection.
    The third argument `true` makes it LOCAL (transaction-scoped),
    equivalent to SET LOCAL.

    Args:
        session: Active async database session
        tenant_id: UUID string of the tenant

    Raises:
        ValueError: If tenant_id is not a valid UUID
    """
    # Validate UUID format
    try:
        UUID(str(tenant_id))
    except ValueError:
        raise ValueError(f"Invalid tenant_id format: {tenant_id}")

    await session.execute(
        text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """
    Get database session with tenant context and audit context set for RLS.

    This dependency:
    1. Creates a new database session
    2. Sets tenant context from request.state.tenant_id
    3. Sets audit context from request.state.audit_context
    4. Yields session for query execution
    5. Commits on success, rollback on error
    6. Closes session

    Usage:
        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            # All queries automatically filtered by tenant via RLS
            result = await db.execute(select(Item))
            return result.scalars().all()
    """
    async with AsyncSessionLocal() as session:
        try:
            # Set tenant context if available
            tenant_id = getattr(request.state, 'tenant_id', None)
            if tenant_id:
                try:
                    await set_tenant_context(session, str(tenant_id))
                    logger.debug(f"Database session created with tenant: {tenant_id}")
                except ValueError:
                    logger.error(f"Invalid tenant_id format: {tenant_id}")
                    raise

            # Set audit context variables for database triggers
            # Using parameterized queries via set_config() to prevent SQL injection
            audit_context = getattr(request.state, 'audit_context', {})
            if audit_context:
                if audit_context.get('user_id'):
                    await session.execute(
                        text("SELECT set_config('app.current_user_id', :user_id, true)"),
                        {"user_id": str(audit_context['user_id'])}
                    )
                if audit_context.get('user_email'):
                    await session.execute(
                        text("SELECT set_config('app.current_user_email', :email, true)"),
                        {"email": audit_context['user_email']}
                    )
                if audit_context.get('ip_address'):
                    await session.execute(
                        text("SELECT set_config('app.client_ip', :ip, true)"),
                        {"ip": audit_context['ip_address']}
                    )
                if audit_context.get('user_agent'):
                    await session.execute(
                        text("SELECT set_config('app.user_agent', :ua, true)"),
                        {"ua": audit_context['user_agent']}
                    )

            yield session
            await session.commit()

        except Exception as e:
            await session.rollback()
            logger.error(f"Database session error: {e}")
            raise

        finally:
            await session.close()


async def get_db_no_tenant() -> AsyncGenerator[AsyncSession, None]:
    """
    Get database session WITHOUT tenant context.

    Use this ONLY for:
    - Superuser/admin operations across tenants
    - System migrations
    - Authentication (before user is known)

    WARNING: RLS policies will not filter data!
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def set_background_audit_context(
    session: AsyncSession, user_id: str, source: str
) -> None:
    """Set audit context for background (non-HTTP) execution.

    Background agents don't have a FastAPI request context, so audit
    variables (user_id, source) must be set explicitly.

    Args:
        session: Database session with tenant context already set
        user_id: User ID for audit trail (typically the agent creator)
        source: Trigger source (e.g., "scheduler", "webhook")
    """
    await session.execute(
        text("SET LOCAL app.audit_user_id = :uid"),
        {"uid": user_id},
    )
    await session.execute(
        text("SET LOCAL app.audit_source = :src"),
        {"src": source},
    )


@asynccontextmanager
async def get_db_for_tenant(tenant_id: str) -> AsyncGenerator[AsyncSession, None]:
    """
    Get database session for a specific tenant (for background jobs).

    Args:
        tenant_id: UUID string of target tenant

    Usage:
        async with get_db_for_tenant("tenant-uuid") as db:
            result = await db.execute(select(Workflow))
    """
    async with AsyncSessionLocal() as session:
        try:
            await set_tenant_context(session, tenant_id)
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """
    Initialize database.
    Create tables and extensions if they don't exist.
    """
    from app.models import Base

    async with engine.begin() as conn:
        # Create pgvector extension
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

        # Create all tables
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database initialized")


async def close_db():
    """
    Close database connections.
    """
    await engine.dispose()
    logger.info("Database connections closed")
