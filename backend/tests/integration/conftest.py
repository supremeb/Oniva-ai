"""
Shared integration test fixtures for Deep Work Architecture E2E tests.

Fixtures provide:
- integration_db: In-memory SQLite with full schema
- session_storage: Real SessionStorage with tmp_path filesystem
- test_session: Pre-populated session with plan.md, todo.md
- test_connector: TestMarketDataConnector registered in ConnectorRegistry
- skill_context: SkillContext with session_id, tenant_id, connectors
- state_machine: StateMachineManager instance
- patch_session_storage_singleton: autouse patch for get_session_storage()
"""

import pytest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB, INET, ARRAY as PG_ARRAY, TSVECTOR
from sqlalchemy import ARRAY as SA_ARRAY
from sqlalchemy.ext.compiler import compiles

from app.models import Base
from app.agents.skills.context import SkillContext
from app.agents.state_machine import StateMachineManager
from app.services.session_storage import SessionStorage


# ---------------------------------------------------------------------------
# SQLite type overrides for ALL PostgreSQL-specific types
# Must be registered before any SQLite engine creates tables.
# ---------------------------------------------------------------------------
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite_integ(type_, compiler, **kw):
    return "JSON"


@compiles(INET, "sqlite")
def compile_inet_sqlite_integ(type_, compiler, **kw):
    return "TEXT"


@compiles(PG_ARRAY, "sqlite")
def compile_pg_array_sqlite_integ(type_, compiler, **kw):
    return "JSON"


@compiles(SA_ARRAY, "sqlite")
def compile_sa_array_sqlite_integ(type_, compiler, **kw):
    return "JSON"


@compiles(TSVECTOR, "sqlite")
def compile_tsvector_sqlite_integ(type_, compiler, **kw):
    return "TEXT"


# pgvector Vector type — compile to TEXT for SQLite
try:
    from pgvector.sqlalchemy import Vector

    @compiles(Vector, "sqlite")
    def compile_vector_sqlite_integ(type_, compiler, **kw):
        return "TEXT"
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Fixtures directory
# ---------------------------------------------------------------------------
FIXTURES_DIR = Path(__file__).parent / "fixtures"
TENANT = "test-tenant-001"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
@pytest.fixture
async def integration_db():
    """Create in-memory SQLite database with full schema."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session_factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# Session Storage
# ---------------------------------------------------------------------------
@pytest.fixture
def session_storage(tmp_path):
    """Real SessionStorage backed by tmp_path filesystem."""
    return SessionStorage(base_path=tmp_path / "sessions", checkpointing_enabled=False)


@pytest.fixture
async def test_session(session_storage):
    """Pre-populated session with plan.md and todo.md from fixture files."""
    session_id = "test-session-001"
    user_id = "test-user-001"

    info = await session_storage.create_session(TENANT, session_id, user_id)

    plan_content = (FIXTURES_DIR / "sample_plan.md").read_text()
    todo_content = (FIXTURES_DIR / "sample_todo.md").read_text()
    await session_storage.write(TENANT, session_id, "plan.md", plan_content)
    await session_storage.write(TENANT, session_id, "todo.md", todo_content)

    return session_id, user_id, info


# ---------------------------------------------------------------------------
# Connector Registry
# ---------------------------------------------------------------------------
@pytest.fixture
def test_connector():
    """Register TestMarketDataConnector and restore registry state on teardown."""
    from app.connectors.test_market_data.connector import TestMarketDataConnector
    from app.connectors.registry import ConnectorRegistry

    # Save existing state
    saved_connectors = dict(ConnectorRegistry._connectors)

    # Register test connector
    connector = TestMarketDataConnector()
    ConnectorRegistry._connectors["test_market_data"] = TestMarketDataConnector

    yield connector

    # Restore state to prevent test pollution
    ConnectorRegistry._connectors = saved_connectors


# ---------------------------------------------------------------------------
# Skill Context
# ---------------------------------------------------------------------------
@pytest.fixture
def skill_context(test_session, test_connector):
    """SkillContext with session_id, tenant_id, and test connector."""
    session_id, user_id, _ = test_session
    return SkillContext(
        user_id=user_id,
        tenant_id="test-tenant-001",
        session_id=session_id,
        connectors={
            "test-market-data-conn": {
                "name": "Test Market Data",
                "provider": "test_market_data",
                "type": "financial",
                "is_verified": True,
                "credentials": {},
                "config": {},
            }
        },
        metadata={"agent_id": "analysis-agent"},
    )


# ---------------------------------------------------------------------------
# State Machine
# ---------------------------------------------------------------------------
@pytest.fixture
def state_machine():
    """StateMachineManager instance."""
    return StateMachineManager()


# ---------------------------------------------------------------------------
# Session Storage Singleton Patch
# ---------------------------------------------------------------------------
STORAGE_PATCH = "app.services.session_storage.get_session_storage"


@pytest.fixture(autouse=True)
def patch_session_storage_singleton(session_storage):
    """Patch get_session_storage() to use the test instance for all tests."""
    with patch(STORAGE_PATCH, return_value=session_storage):
        yield
