"""Pytest configuration and fixtures"""

import os

# Before any app import: Settings must not read a developer's backend/.env during tests.
os.environ["ONIVA_ENV_FILE"] = ""
# litellm (imported by app code) calls load_dotenv() in its default DEV mode, which copies backend/.env
# into os.environ and defeats the line above; PRODUCTION mode skips that.
os.environ["LITELLM_MODE"] = "PRODUCTION"

import pytest
import asyncio
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base
from sqlalchemy.dialects.postgresql import JSONB, INET, TSVECTOR
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.types import ARRAY as GenericARRAY
from sqlalchemy.ext.compiler import compiles
from pgvector.sqlalchemy import Vector

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"

@compiles(INET, "sqlite")
def compile_inet_sqlite(type_, compiler, **kw):
    return "TEXT"

@compiles(PG_ARRAY, "sqlite")
def compile_pg_array_sqlite(type_, compiler, **kw):
    return "JSON"

@compiles(GenericARRAY, "sqlite")
def compile_generic_array_sqlite(type_, compiler, **kw):
    return "JSON"

@compiles(TSVECTOR, "sqlite")
def compile_tsvector_sqlite(type_, compiler, **kw):
    return "TEXT"

@compiles(Vector, "sqlite")
def compile_vector_sqlite(type_, compiler, **kw):
    return "TEXT"


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def test_db():
    """Create test database session"""
    # Use in-memory SQLite for testing
    import sqlite3

    # PostgreSQL aggregate polyfill for SQLite
    class BoolOr:
        def __init__(self):
            self.result = False
        def step(self, value):
            if value:
                self.result = True
        def finalize(self):
            return self.result

    def _creator():
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.create_aggregate("bool_or", 1, BoolOr)
        return conn

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        creator=_creator,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def test_user_id():
    """Test user ID"""
    return uuid.UUID("00000000-0000-0000-0000-000000000000")
