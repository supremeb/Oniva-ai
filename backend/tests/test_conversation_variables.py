"""
Unit tests for ConversationVariablesService with persistence.

Tests cover:
- Basic variable operations (get, set, reset)
- Session state management
- Dirty tracking for write coalescing
- Filesystem persistence via SessionStorage
- Workflow namespace isolation
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
import json

from app.services.conversation_variables import (
    ConversationVariablesService,
    VariableType,
    VariableDefinition,
)

TENANT = "test-tenant"


@pytest.fixture
def service():
    """Create a fresh service instance for each test."""
    return ConversationVariablesService()


class TestBasicOperations:
    """Tests for basic variable operations."""

    def test_set_and_get_variable(self, service):
        """Test setting and getting a variable."""
        service.set_variable("session_1", "workflow_1", "counter", 42)
        value = service.get_variable("session_1", "workflow_1", "counter")
        assert value == 42

    def test_get_nonexistent_variable_returns_default(self, service):
        """Test that getting a nonexistent variable returns default."""
        value = service.get_variable("session_1", "workflow_1", "missing", default="default_value")
        assert value == "default_value"

    def test_get_nonexistent_variable_no_default_returns_none(self, service):
        """Test that getting a nonexistent variable without default returns None."""
        value = service.get_variable("session_1", "workflow_1", "missing")
        assert value is None

    def test_reset_variable(self, service):
        """Test resetting a variable to default."""
        service.set_variable("session_1", "workflow_1", "to_reset", "value")
        assert service.get_variable("session_1", "workflow_1", "to_reset") == "value"

        service.reset_variable("session_1", "workflow_1", "to_reset", default=None)
        assert service.get_variable("session_1", "workflow_1", "to_reset") is None

    def test_reset_variable_to_new_default(self, service):
        """Test resetting a variable to a new default value."""
        service.set_variable("session_1", "workflow_1", "counter", 100)
        service.reset_variable("session_1", "workflow_1", "counter", default=0)
        assert service.get_variable("session_1", "workflow_1", "counter") == 0

    def test_get_all_variables(self, service):
        """Test getting all variables for a session."""
        service.set_variable("session_1", "workflow_1", "var1", "value1")
        service.set_variable("session_1", "workflow_1", "var2", "value2")
        service.set_variable("session_1", "workflow_1", "var3", 123)

        all_vars = service.get_all_variables("session_1", "workflow_1")

        assert "var1" in all_vars
        assert "var2" in all_vars
        assert "var3" in all_vars
        assert all_vars["var1"] == "value1"
        assert all_vars["var2"] == "value2"
        assert all_vars["var3"] == 123


class TestSessionIsolation:
    """Tests for session and workflow isolation."""

    def test_sessions_are_isolated(self, service):
        """Test that variables in different sessions don't interfere."""
        service.set_variable("session_1", "workflow_1", "shared_name", "value_1")
        service.set_variable("session_2", "workflow_1", "shared_name", "value_2")

        assert service.get_variable("session_1", "workflow_1", "shared_name") == "value_1"
        assert service.get_variable("session_2", "workflow_1", "shared_name") == "value_2"

    def test_workflows_are_isolated(self, service):
        """Test that variables in different workflows don't interfere."""
        service.set_variable("session_1", "workflow_a", "shared_name", "value_a")
        service.set_variable("session_1", "workflow_b", "shared_name", "value_b")

        assert service.get_variable("session_1", "workflow_a", "shared_name") == "value_a"
        assert service.get_variable("session_1", "workflow_b", "shared_name") == "value_b"


class TestSessionManagement:
    """Tests for session management."""

    def test_get_or_create_session(self, service):
        """Test getting or creating a session."""
        state = service.get_or_create_session("session_1", "workflow_1")

        assert state.session_id == "session_1"
        assert state.workflow_id == "workflow_1"
        assert state.turn_number == 0

    def test_get_or_create_with_variable_definitions(self, service):
        """Test creating session with variable definitions."""
        definitions = [
            VariableDefinition(name="counter", type=VariableType.NUMBER, default=0),
            VariableDefinition(name="items", type=VariableType.ARRAY, default=[]),
        ]

        state = service.get_or_create_session("session_1", "workflow_1", definitions)

        assert "counter" in state.variables
        assert "items" in state.variables
        assert state.variables["counter"].current.value == 0
        assert state.variables["items"].current.value == []

    def test_start_turn_increments_turn_number(self, service):
        """Test that start_turn increments the turn number."""
        service.get_or_create_session("session_1", "workflow_1")

        turn1 = service.start_turn("session_1", "workflow_1")
        assert turn1 == 1

        turn2 = service.start_turn("session_1", "workflow_1")
        assert turn2 == 2


class TestDirtyTracking:
    """Tests for dirty session tracking (write coalescing)."""

    def test_mark_dirty_explicit(self, service):
        """Test explicitly marking a session as dirty."""
        service.mark_dirty(TENANT, "session_1", "workflow_1")
        assert f"{TENANT}:workflow_1:session_1" in service._dirty_sessions

    def test_dirty_sessions_start_empty(self, service):
        """Test that dirty sessions set starts empty."""
        assert len(service._dirty_sessions) == 0


class TestPersistence:
    """Tests for filesystem persistence via SessionStorage."""

    @pytest.mark.asyncio
    async def test_flush_session_stores_to_filesystem(self, service):
        """Test that flushing a session writes to SessionStorage."""
        mock_storage = MagicMock()
        mock_storage.read = AsyncMock(return_value=None)
        mock_storage.write = AsyncMock()

        service.set_storage(mock_storage)
        service.set_variable("session_1", "workflow_1", "var1", "value1")
        service.set_variable("session_1", "workflow_1", "var2", 42)
        service.mark_dirty(TENANT, "session_1", "workflow_1")

        result = await service.flush_session(TENANT, "session_1", "workflow_1")

        assert result is True
        mock_storage.write.assert_called_once()

        # Verify the state.json was written with correct structure
        call_args = mock_storage.write.call_args
        assert call_args[0][0] == TENANT
        assert call_args[0][1] == "session_1"
        assert call_args[0][2] == "state.json"

        # Parse the JSON content
        written_content = json.loads(call_args[0][3])
        assert "variables_by_workflow" in written_content
        assert "workflow_1" in written_content["variables_by_workflow"]

    @pytest.mark.asyncio
    async def test_flush_session_clears_dirty_flag(self, service):
        """Test that flushing clears the dirty flag."""
        mock_storage = MagicMock()
        mock_storage.read = AsyncMock(return_value=None)
        mock_storage.write = AsyncMock()

        service.set_storage(mock_storage)
        service.set_variable("session_1", "workflow_1", "var", "value")
        service.mark_dirty(TENANT, "session_1", "workflow_1")

        assert f"{TENANT}:workflow_1:session_1" in service._dirty_sessions

        await service.flush_session(TENANT, "session_1", "workflow_1")

        assert f"{TENANT}:workflow_1:session_1" not in service._dirty_sessions

    @pytest.mark.asyncio
    async def test_flush_session_without_storage_returns_false(self, service):
        """Test that flushing without storage set returns False."""
        service.set_variable("session_1", "workflow_1", "var", "value")
        service.mark_dirty(TENANT, "session_1", "workflow_1")

        result = await service.flush_session(TENANT, "session_1", "workflow_1")

        assert result is False

    @pytest.mark.asyncio
    async def test_flush_clean_session_returns_false(self, service):
        """Test that flushing a clean session returns False (not dirty)."""
        mock_storage = MagicMock()
        service.set_storage(mock_storage)

        # Session is not dirty (mark_dirty not called)
        result = await service.flush_session(TENANT, "clean_session", "workflow_1")

        assert result is False
        mock_storage.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_persisted_session(self, service):
        """Test loading a session from filesystem."""
        # The persisted state structure stores full session data under workflow namespace
        persisted_state = json.dumps({
            "variables_by_workflow": {
                "workflow_1": {
                    "session_id": "session_1",
                    "workflow_id": "workflow_1",
                    "turn_number": 3,
                    "variables": {
                        "loaded_var1": {
                            "name": "loaded_var1",
                            "current": {
                                "value": "persisted_value1",
                                "type": "string",
                                "updated_at": "2026-01-01T00:00:00",
                                "turn_number": 1
                            },
                            "history": []
                        }
                    },
                    "created_at": "2026-01-01T00:00:00",
                    "updated_at": "2026-01-01T00:00:00"
                }
            }
        })

        mock_storage = MagicMock()
        mock_storage.read = AsyncMock(return_value=persisted_state)

        service.set_storage(mock_storage)

        state = await service.load_persisted_session(TENANT, "session_1", "workflow_1")

        assert state is not None
        assert state.session_id == "session_1"

    @pytest.mark.asyncio
    async def test_load_persisted_session_empty_state(self, service):
        """Test loading from empty state."""
        mock_storage = MagicMock()
        mock_storage.read = AsyncMock(return_value=None)

        service.set_storage(mock_storage)

        result = await service.load_persisted_session(TENANT, "session_1", "workflow_1")

        assert result is None

    @pytest.mark.asyncio
    async def test_load_persisted_session_without_storage(self, service):
        """Test loading without storage returns None."""
        result = await service.load_persisted_session(TENANT, "session_1", "workflow_1")
        assert result is None

    @pytest.mark.asyncio
    async def test_flush_all_flushes_dirty_sessions(self, service):
        """Test that flush_all flushes all dirty sessions."""
        mock_storage = MagicMock()
        mock_storage.read = AsyncMock(return_value=None)
        mock_storage.write = AsyncMock()

        service.set_storage(mock_storage)
        service.set_variable("session_1", "workflow_1", "var", "value1")
        service.set_variable("session_2", "workflow_1", "var", "value2")
        service.mark_dirty(TENANT, "session_1", "workflow_1")
        service.mark_dirty(TENANT, "session_2", "workflow_1")

        flushed = await service.flush_all()

        assert flushed == 2
        assert len(service._dirty_sessions) == 0


class TestWorkflowNamespacing:
    """Tests for workflow namespace isolation in persistence."""

    @pytest.mark.asyncio
    async def test_different_workflows_store_to_separate_namespaces(self, service):
        """Test that different workflows store to separate namespaces."""
        mock_storage = MagicMock()
        mock_storage.read = AsyncMock(return_value=None)
        mock_storage.write = AsyncMock()

        service.set_storage(mock_storage)

        # Set variable for workflow_a
        service.set_variable("session_1", "workflow_a", "result", "result_a")
        service.mark_dirty(TENANT, "session_1", "workflow_a")
        await service.flush_session(TENANT, "session_1", "workflow_a")

        # Verify workflow_a namespace was used
        call_args = mock_storage.write.call_args
        written_content = json.loads(call_args[0][3])
        assert "workflow_a" in written_content["variables_by_workflow"]


class TestExportImport:
    """Tests for session export/import functionality."""

    def test_export_session(self, service):
        """Test exporting a session to dictionary."""
        service.set_variable("session_1", "workflow_1", "var1", "value1")
        service.set_variable("session_1", "workflow_1", "var2", [1, 2, 3])

        exported = service.export_session("session_1", "workflow_1")

        assert exported is not None
        assert "variables" in exported
        assert "var1" in exported["variables"]
        assert "var2" in exported["variables"]

    def test_export_session_nonexistent(self, service):
        """Test exporting a nonexistent session."""
        exported = service.export_session("nonexistent", "workflow_1")
        assert exported is None

    def test_import_session(self, service):
        """Test importing session data."""
        data = {
            "session_id": "imported_session",
            "workflow_id": "imported_workflow",
            "turn_number": 5,
            "variables": {},
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }

        state = service.import_session(data)

        assert state is not None
        assert state.session_id == "imported_session"
        assert state.workflow_id == "imported_workflow"
        assert state.turn_number == 5


class TestTypeInference:
    """Tests for variable type inference."""

    def test_infer_string_type(self, service):
        """Test string type inference."""
        service.set_variable("session_1", "workflow_1", "str_var", "hello")
        state = service.get_session("session_1", "workflow_1")
        assert state.variables["str_var"].current.type == VariableType.STRING

    def test_infer_number_type(self, service):
        """Test number type inference."""
        service.set_variable("session_1", "workflow_1", "int_var", 42)
        service.set_variable("session_1", "workflow_1", "float_var", 3.14)
        state = service.get_session("session_1", "workflow_1")
        assert state.variables["int_var"].current.type == VariableType.NUMBER
        assert state.variables["float_var"].current.type == VariableType.NUMBER

    def test_infer_boolean_type(self, service):
        """Test boolean type inference."""
        service.set_variable("session_1", "workflow_1", "bool_var", True)
        state = service.get_session("session_1", "workflow_1")
        assert state.variables["bool_var"].current.type == VariableType.BOOLEAN

    def test_infer_array_type(self, service):
        """Test array type inference."""
        service.set_variable("session_1", "workflow_1", "list_var", [1, 2, 3])
        state = service.get_session("session_1", "workflow_1")
        assert state.variables["list_var"].current.type == VariableType.ARRAY

    def test_infer_object_type(self, service):
        """Test object type inference."""
        service.set_variable("session_1", "workflow_1", "dict_var", {"key": "value"})
        state = service.get_session("session_1", "workflow_1")
        assert state.variables["dict_var"].current.type == VariableType.OBJECT


class TestVariableHistory:
    """Tests for variable history tracking."""

    def test_history_is_tracked(self, service):
        """Test that variable history is tracked."""
        service.set_variable("session_1", "workflow_1", "counter", 1)
        service.set_variable("session_1", "workflow_1", "counter", 2)
        service.set_variable("session_1", "workflow_1", "counter", 3)

        state = service.get_session("session_1", "workflow_1")
        var_history = state.variables["counter"]

        assert var_history.current.value == 3
        assert len(var_history.history) == 2
        assert var_history.history[0].value == 1
        assert var_history.history[1].value == 2
