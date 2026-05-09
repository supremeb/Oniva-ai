"""Tests for per-agent file isolation in SessionStorage.

Verifies that agent-scoped paths, cross-agent isolation, session-level fallback,
shared artifact access, and path traversal protection all work correctly.
"""
import pytest
from pathlib import Path

from app.services.session_storage import SessionStorage


@pytest.fixture
def storage(tmp_path: Path) -> SessionStorage:
    """Create a SessionStorage instance with a temp base path."""
    return SessionStorage(base_path=tmp_path, checkpointing_enabled=False)


TENANT = "tenant-abc"
SESSION = "session-123"


@pytest.mark.asyncio
async def test_agent_scoped_write_read(storage: SessionStorage):
    """Write with agent_id, read back — file is in agents/{agent_id}/ subdir."""
    await storage.write(TENANT, SESSION, "todo.md", "# Agent A tasks", agent_id="agent-A")
    content = await storage.read(TENANT, SESSION, "todo.md", agent_id="agent-A")
    assert content == "# Agent A tasks"

    # Verify the file is physically in the agents subdirectory
    expected_path = storage.base_path / TENANT / SESSION / "agents" / "agent-A" / "todo.md"
    assert expected_path.exists()


@pytest.mark.asyncio
async def test_agent_isolation(storage: SessionStorage):
    """Write with agent-A, verify agent-B cannot read it."""
    await storage.write(TENANT, SESSION, "secret.txt", "agent-A data", agent_id="agent-A")

    # Agent-B sees empty string (file not found in its scope)
    content = await storage.read(TENANT, SESSION, "secret.txt", agent_id="agent-B")
    assert content == ""

    # Agent-A can still read its own file
    content = await storage.read(TENANT, SESSION, "secret.txt", agent_id="agent-A")
    assert content == "agent-A data"


@pytest.mark.asyncio
async def test_session_level_fallback(storage: SessionStorage):
    """Omit agent_id — file goes to session-level path (backward compatible)."""
    await storage.write(TENANT, SESSION, "plan.md", "# Session plan")
    content = await storage.read(TENANT, SESSION, "plan.md")
    assert content == "# Session plan"

    # Verify it's at session level, not in agents/
    expected_path = storage.base_path / TENANT / SESSION / "plan.md"
    assert expected_path.exists()


@pytest.mark.asyncio
async def test_shared_via_session_path(storage: SessionStorage):
    """Write without agent_id to artifacts/, read from different context also without agent_id."""
    await storage.write(TENANT, SESSION, "artifacts/report.json", '{"result": "ok"}')

    # Read without agent_id — sees the shared artifact
    content = await storage.read(TENANT, SESSION, "artifacts/report.json")
    assert content == '{"result": "ok"}'

    # list_files without agent_id includes everything (orchestrator view)
    files = await storage.list_files(TENANT, SESSION)
    assert "artifacts/report.json" in files


@pytest.mark.asyncio
async def test_path_traversal_agent_id(storage: SessionStorage):
    """agent_id='../escape' raises ValueError from _sanitize."""
    with pytest.raises(ValueError, match="Invalid agent_id"):
        await storage.write(TENANT, SESSION, "file.txt", "data", agent_id="../escape")


@pytest.mark.asyncio
async def test_sanitize_rejects_dots(storage: SessionStorage):
    """agent_id='analysis.v2' raises ValueError — dots not allowed in sanitize."""
    with pytest.raises(ValueError, match="Invalid agent_id"):
        await storage.write(TENANT, SESSION, "file.txt", "data", agent_id="analysis.v2")
