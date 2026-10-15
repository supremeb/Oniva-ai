"""pgvector nearest-neighbour search (skipped where the extension is unavailable)."""

import pytest
from sqlalchemy import create_engine, text


def test_nearest_neighbour_returns_closest(pg_server, pg_has_vector):
    if not pg_has_vector:
        pytest.skip("pgvector extension not available for this Postgres; CI runs this test")
    pg_server.recreate_db("oniva_vec")
    engine = create_engine(pg_server.url("oniva_vec"))
    try:
        with engine.begin() as c:
            c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            c.execute(text("CREATE TABLE items (id int PRIMARY KEY, embedding vector(3))"))
            c.execute(text("INSERT INTO items VALUES (1,'[1,0,0]'),(2,'[0,1,0]'),(3,'[0.9,0.1,0]')"))
            c.execute(text("CREATE INDEX ON items USING hnsw (embedding vector_cosine_ops)"))
            rows = c.execute(text(
                "SELECT id FROM items ORDER BY embedding <=> '[1,0.05,0]' LIMIT 2")).fetchall()
        assert [r[0] for r in rows] == [1, 3]
    finally:
        engine.dispose()
