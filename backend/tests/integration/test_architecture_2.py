"""
Integration tests features.

Tests verify that all Phase 1-3 features work correctly when enabled:
- USE_DURABLE_WORKFLOWS=True - DBOS checkpointing
- USE_STATE_MACHINE=True - Tool masking
- USE_SESSION_FILESYSTEM=True - 8+ hour sessions

These tests use real database connections and filesystem operations.
"""
import pytest
import json
import tempfile
import shutil
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.services.durable_workflow_engine import DurableWorkflowEngine
from app.services.session_storage import SessionStorage
from app.agents.state_machine import (
    StateMachineManager,
    StateContext,
    WorkflowState,
    StateTransitionError,
    InvalidToolError,
)

# SQLite type compatibility for tests
from sqlalchemy.dialects.postgresql import JSONB, INET
from sqlalchemy.ext.compiler import compiles

# Tenant ID constant for all session storage tests
TENANT = "test-tenant"


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"


@compiles(INET, "sqlite")
def compile_inet_sqlite(type_, compiler, **kw):
    return "TEXT"


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
async def integration_db():
    """Create a test database for integration tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
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
def temp_session_dir():
    """Create a temporary directory for session storage tests."""
    temp_dir = tempfile.mkdtemp(prefix="oniva_test_")
    yield Path(temp_dir)
    # Cleanup
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def session_storage(temp_session_dir):
    """Create a SessionStorage instance with a temporary directory."""
    return SessionStorage(base_path=temp_session_dir, checkpointing_enabled=False)


@pytest.fixture
def state_machine():
    """Create a StateMachineManager instance."""
    return StateMachineManager()


@pytest.fixture
def test_user_id():
    """Test user ID."""
    return "test-user-123"


@pytest.fixture
def test_tenant_id():
    """Test tenant ID."""
    return uuid4()


@pytest.fixture
def sample_workflow_spec():
    """Sample workflow specification for testing."""
    return {
        "nodes": [
            {
                "id": "node_1",
                "type": "input",
                "data": {"prompt": "Enter stock ticker"},
            },
            {
                "id": "node_2",
                "type": "data_retrieval",
                "data": {"agent": "data-retrieval", "action": "fetch_stock_price"},
            },
            {
                "id": "node_3",
                "type": "analysis",
                "data": {"agent": "analysis", "action": "calculate_metrics"},
            },
            {
                "id": "node_4",
                "type": "output",
                "data": {"format": "markdown"},
            },
        ],
        "edges": [
            {"id": "e1", "source": "node_1", "target": "node_2"},
            {"id": "e2", "source": "node_2", "target": "node_3"},
            {"id": "e3", "source": "node_3", "target": "node_4"},
        ],
    }


# =============================================================================
# Durable Workflow Engine Tests
# =============================================================================


class TestDurableWorkflowsIntegration:
    """Test DBOS checkpointing with database persistence."""

    @pytest.mark.asyncio
    async def test_checkpoint_creation(
        self, integration_db, test_user_id, test_tenant_id
    ):
        """Test that checkpoints are created in the database."""
        engine = DurableWorkflowEngine(
            db=integration_db,
            user_id=test_user_id,
            tenant_id=test_tenant_id,
        )

        # Stub out RLS context setting (not supported in SQLite)
        engine._ensure_rls_context = AsyncMock()

        workflow_uuid = uuid4()

        # Create a checkpoint
        checkpoint = await engine._create_checkpoint(
            workflow_uuid=workflow_uuid,
            step_number=0,
            step_name="test:node_1",
            node_id="node_1",
            input_data={"test": "data"},
        )

        assert checkpoint is not None
        assert checkpoint.workflow_uuid == workflow_uuid
        assert checkpoint.step_number == 0
        assert checkpoint.step_name == "test:node_1"
        assert checkpoint.status == "running"
        assert checkpoint.input_data == {"test": "data"}

    @pytest.mark.asyncio
    async def test_checkpoint_completion(
        self, integration_db, test_user_id, test_tenant_id
    ):
        """Test marking checkpoint as completed."""
        engine = DurableWorkflowEngine(
            db=integration_db,
            user_id=test_user_id,
            tenant_id=test_tenant_id,
        )

        # Stub out RLS context setting (not supported in SQLite)
        engine._ensure_rls_context = AsyncMock()

        workflow_uuid = uuid4()

        # Create and complete checkpoint
        checkpoint = await engine._create_checkpoint(
            workflow_uuid=workflow_uuid,
            step_number=0,
            step_name="test:node_1",
            node_id="node_1",
            input_data={},
        )

        await engine._complete_checkpoint(
            checkpoint=checkpoint,
            output_data={"result": "success"},
        )

        assert checkpoint.status == "completed"
        assert checkpoint.output_data == {"result": "success"}
        assert checkpoint.completed_at is not None
        assert checkpoint.elapsed_ms >= 0

    @pytest.mark.asyncio
    async def test_checkpoint_failure(
        self, integration_db, test_user_id, test_tenant_id
    ):
        """Test marking checkpoint as failed."""
        engine = DurableWorkflowEngine(
            db=integration_db,
            user_id=test_user_id,
            tenant_id=test_tenant_id,
        )

        # Stub out RLS context setting (not supported in SQLite)
        engine._ensure_rls_context = AsyncMock()

        workflow_uuid = uuid4()

        checkpoint = await engine._create_checkpoint(
            workflow_uuid=workflow_uuid,
            step_number=0,
            step_name="test:node_1",
            node_id="node_1",
            input_data={},
        )

        await engine._fail_checkpoint(
            checkpoint=checkpoint,
            error_message="Test error message",
        )

        assert checkpoint.status == "failed"
        assert checkpoint.error_message == "Test error message"
        assert checkpoint.completed_at is not None

    @pytest.mark.asyncio
    async def test_get_last_completed_checkpoint(
        self, integration_db, test_user_id, test_tenant_id
    ):
        """Test retrieving the last completed checkpoint."""
        engine = DurableWorkflowEngine(
            db=integration_db,
            user_id=test_user_id,
            tenant_id=test_tenant_id,
        )

        # Stub out RLS context setting (not supported in SQLite)
        engine._ensure_rls_context = AsyncMock()

        workflow_uuid = uuid4()

        # Create multiple checkpoints
        for i in range(3):
            checkpoint = await engine._create_checkpoint(
                workflow_uuid=workflow_uuid,
                step_number=i,
                step_name=f"test:node_{i}",
                node_id=f"node_{i}",
                input_data={"step": i},
            )
            await engine._complete_checkpoint(checkpoint, {"step": i, "done": True})

        # Get last checkpoint
        last = await engine._get_last_completed_checkpoint(workflow_uuid)

        assert last is not None
        assert last.step_number == 2
        assert last.status == "completed"

    @pytest.mark.asyncio
    async def test_replay_workflow_returns_all_checkpoints(
        self, integration_db, test_user_id, test_tenant_id
    ):
        """Test that replay returns all checkpoints for audit."""
        engine = DurableWorkflowEngine(
            db=integration_db,
            user_id=test_user_id,
            tenant_id=test_tenant_id,
        )

        # Stub out RLS context setting (not supported in SQLite)
        engine._ensure_rls_context = AsyncMock()

        workflow_uuid = uuid4()

        # Create checkpoints
        for i in range(3):
            checkpoint = await engine._create_checkpoint(
                workflow_uuid=workflow_uuid,
                step_number=i,
                step_name=f"test:node_{i}",
                node_id=f"node_{i}",
                input_data={"step": i},
            )
            await engine._complete_checkpoint(checkpoint, {"result": f"output_{i}"})

        # Replay workflow
        replay_data = await engine.replay_workflow(workflow_uuid)

        assert len(replay_data) == 3
        assert replay_data[0]["step_number"] == 0
        assert replay_data[1]["step_number"] == 1
        assert replay_data[2]["step_number"] == 2
        assert all(cp["status"] == "completed" for cp in replay_data)

    @pytest.mark.asyncio
    async def test_recovery_from_checkpoint(
        self, integration_db, test_user_id, test_tenant_id
    ):
        """Test workflow recovery finds correct checkpoint."""
        engine = DurableWorkflowEngine(
            db=integration_db,
            user_id=test_user_id,
            tenant_id=test_tenant_id,
        )

        # Stub out RLS context setting (not supported in SQLite)
        engine._ensure_rls_context = AsyncMock()

        workflow_uuid = uuid4()

        # Create 2 completed checkpoints and 1 failed
        for i in range(2):
            checkpoint = await engine._create_checkpoint(
                workflow_uuid=workflow_uuid,
                step_number=i,
                step_name=f"test:node_{i}",
                node_id=f"node_{i}",
                input_data={"step": i},
            )
            await engine._complete_checkpoint(checkpoint, {"result": i})

        # Third checkpoint failed
        checkpoint3 = await engine._create_checkpoint(
            workflow_uuid=workflow_uuid,
            step_number=2,
            step_name="test:node_2",
            node_id="node_2",
            input_data={"step": 2},
        )
        await engine._fail_checkpoint(checkpoint3, "Simulated failure")

        # Get last completed - should be step 1
        last = await engine._get_last_completed_checkpoint(workflow_uuid)

        assert last is not None
        assert last.step_number == 1
        assert last.status == "completed"


# =============================================================================
# State Machine Tests
# =============================================================================


class TestStateMachineIntegration:
    """Test state machine tool enforcement."""

    def test_valid_tool_in_data_gathering(self, state_machine):
        """Test that data gathering tools are valid in DATA_GATHERING state."""
        valid = state_machine.is_tool_valid(
            "fetch_market_data", WorkflowState.DATA_GATHERING
        )
        assert valid is True

    def test_invalid_tool_blocked(self, state_machine):
        """Test that invalid tools are blocked in each state."""
        # In DATA_GATHERING state, email tools should be blocked
        valid = state_machine.is_tool_valid("send_email", WorkflowState.DATA_GATHERING)
        assert valid is False

    def test_validate_tool_access_raises_error(self, state_machine):
        """Test that validate_tool_access raises InvalidToolError for invalid tools."""
        with pytest.raises(InvalidToolError) as exc_info:
            state_machine.validate_tool_access(
                "send_email", WorkflowState.DATA_GATHERING, raise_on_invalid=True
            )

        assert exc_info.value.tool_name == "send_email"
        assert exc_info.value.current_state == WorkflowState.DATA_GATHERING

    def test_valid_state_transition(self, state_machine):
        """Test valid state transitions."""
        # DATA_GATHERING -> ANALYSIS is valid
        valid = state_machine.validate_transition(
            WorkflowState.DATA_GATHERING,
            WorkflowState.ANALYSIS,
            raise_on_invalid=False,
        )
        assert valid is True

    def test_invalid_state_transition_blocked(self, state_machine):
        """Test that invalid state transitions are blocked."""
        # COMPLETED -> DATA_GATHERING is invalid (terminal state)
        valid = state_machine.validate_transition(
            WorkflowState.COMPLETED,
            WorkflowState.DATA_GATHERING,
            raise_on_invalid=False,
        )
        assert valid is False

    def test_invalid_transition_raises_error(self, state_machine):
        """Test that invalid transitions raise StateTransitionError."""
        with pytest.raises(StateTransitionError) as exc_info:
            state_machine.validate_transition(
                WorkflowState.INITIALIZED,
                WorkflowState.COMPLETED,
                raise_on_invalid=True,
            )

        assert exc_info.value.current_state == WorkflowState.INITIALIZED
        assert exc_info.value.requested_state == WorkflowState.COMPLETED

    def test_state_transition_updates_context(self, state_machine):
        """Test that transitions update the StateContext."""
        context = StateContext(workflow_id="test-workflow")

        # Transition from INITIALIZED to DATA_GATHERING
        context = state_machine.transition(
            context, WorkflowState.DATA_GATHERING, reason="Starting data fetch"
        )

        assert context.current_state == WorkflowState.DATA_GATHERING
        assert context.previous_state == WorkflowState.INITIALIZED
        assert context.transition_count == 1
        assert len(context.state_history) == 1
        assert context.state_history[0]["from"] == "initialized"
        assert context.state_history[0]["to"] == "data_gathering"

    def test_state_context_serialization(self):
        """Test StateContext can be serialized and deserialized."""
        context = StateContext(
            current_state=WorkflowState.ANALYSIS,
            previous_state=WorkflowState.DATA_GATHERING,
            workflow_id="test-123",
            transition_count=2,
        )

        # Serialize
        data = context.to_dict()

        assert data["current_state"] == "analysis"
        assert data["previous_state"] == "data_gathering"
        assert data["workflow_id"] == "test-123"

        # Deserialize
        restored = StateContext.from_dict(data)

        assert restored.current_state == WorkflowState.ANALYSIS
        assert restored.previous_state == WorkflowState.DATA_GATHERING
        assert restored.workflow_id == "test-123"

    def test_all_states_have_defined_tools(self, state_machine):
        """Test that all workflow states have defined tool sets."""
        for state in WorkflowState:
            tools = state_machine.get_valid_tools(state)
            assert isinstance(tools, set)
            # All states should have at least read_plan access (except maybe COMPLETED/FAILED)
            if state not in {WorkflowState.COMPLETED, WorkflowState.FAILED}:
                assert len(tools) > 0

    def test_terminal_states_have_no_transitions(self, state_machine):
        """Test that terminal states have no valid transitions."""
        completed_transitions = state_machine.get_valid_transitions(
            WorkflowState.COMPLETED
        )
        failed_transitions = state_machine.get_valid_transitions(WorkflowState.FAILED)

        assert len(completed_transitions) == 0
        assert len(failed_transitions) == 0


# =============================================================================
# Filesystem Session Storage Tests
# =============================================================================


class TestFilesystemSessionsIntegration:
    """Test filesystem-backed sessions."""

    @pytest.mark.asyncio
    async def test_create_session(self, session_storage, test_user_id):
        """Test creating a new session."""
        session_id = f"session_{uuid4().hex[:8]}"

        info = await session_storage.create_session(TENANT, session_id, test_user_id)

        assert info.session_id == session_id
        assert info.user_id == test_user_id
        assert info.status == "active"
        assert info.file_count == 4  # plan.md, todo.md, state.json, logs/history.md

    @pytest.mark.asyncio
    async def test_session_read_write(self, session_storage, test_user_id):
        """Test reading and writing files in a session."""
        session_id = f"session_{uuid4().hex[:8]}"
        await session_storage.create_session(TENANT, session_id, test_user_id)

        # Write custom content
        await session_storage.write(TENANT, session_id, "plan.md", "# My Custom Plan\n\nAnalyze AAPL stock")

        # Read it back
        content = await session_storage.read(TENANT, session_id, "plan.md")

        assert "My Custom Plan" in content
        assert "AAPL" in content

    @pytest.mark.asyncio
    async def test_session_default_files_created(self, session_storage, test_user_id):
        """Test that default files are created on session creation."""
        session_id = f"session_{uuid4().hex[:8]}"
        await session_storage.create_session(TENANT, session_id, test_user_id)

        # Check all default files exist
        plan = await session_storage.read(TENANT, session_id, "plan.md")
        todo = await session_storage.read(TENANT, session_id, "todo.md")
        state = await session_storage.read(TENANT, session_id, "state.json")
        history = await session_storage.read(TENANT, session_id, "logs/history.md")

        assert "Session Plan" in plan
        assert "Todo List" in todo
        assert json.loads(state)["status"] == "active"
        assert "Session History" in history

    @pytest.mark.asyncio
    async def test_session_recitation_context(self, session_storage, test_user_id):
        """Test Manus AI-style recitation context generation."""
        session_id = f"session_{uuid4().hex[:8]}"
        await session_storage.create_session(TENANT, session_id, test_user_id)

        # Write plan and todo with NON-default content
        await session_storage.write(
            TENANT, session_id, "plan.md", "# Analysis Plan\n\n- Analyze AAPL stock performance"
        )
        await session_storage.write(
            TENANT,
            session_id,
            "todo.md",
            "- [ ] Fetch price data\n- [ ] Calculate P/E ratio\n- [ ] Generate report",
        )

        # Get recitation context
        context = await session_storage.get_recitation_context(TENANT, session_id)

        assert "Analysis Plan" in context
        assert "AAPL" in context
        assert "Fetch price data" in context
        assert "Calculate P/E ratio" in context

    @pytest.mark.asyncio
    async def test_session_state_updates(self, session_storage, test_user_id):
        """Test updating session state."""
        session_id = f"session_{uuid4().hex[:8]}"
        await session_storage.create_session(TENANT, session_id, test_user_id)

        # Update state
        new_state = await session_storage.update_state(
            TENANT, session_id, {"step": 3, "current_agent": "analysis"}
        )

        assert new_state["step"] == 3
        assert new_state["current_agent"] == "analysis"
        assert "updated_at" in new_state

    @pytest.mark.asyncio
    async def test_session_log_action(self, session_storage, test_user_id):
        """Test logging actions to session history."""
        session_id = f"session_{uuid4().hex[:8]}"
        await session_storage.create_session(TENANT, session_id, test_user_id)

        # Log an action
        await session_storage.log_action(
            TENANT, session_id, "Fetched AAPL price", "Current price: $150.25"
        )

        # Check history
        history = await session_storage.read(TENANT, session_id, "logs/history.md")

        assert "Fetched AAPL price" in history
        assert "$150.25" in history

    @pytest.mark.asyncio
    async def test_session_error_logging(self, session_storage, test_user_id):
        """Test error logging in sessions."""
        session_id = f"session_{uuid4().hex[:8]}"
        await session_storage.create_session(TENANT, session_id, test_user_id)

        # Log an error
        await session_storage.log_error(
            TENANT, session_id, "API rate limit exceeded", "Traceback: ..."
        )

        # Check error log
        errors = await session_storage.read(TENANT, session_id, "logs/errors.log")

        assert "API rate limit exceeded" in errors

    @pytest.mark.asyncio
    async def test_session_list_files(self, session_storage, test_user_id):
        """Test listing all files in a session."""
        session_id = f"session_{uuid4().hex[:8]}"
        await session_storage.create_session(TENANT, session_id, test_user_id)

        # Add custom file
        await session_storage.write(TENANT, session_id, "analysis/report.md", "# Report")

        files = await session_storage.list_files(TENANT, session_id)

        assert "plan.md" in files
        assert "todo.md" in files
        assert "state.json" in files
        assert "logs/history.md" in files
        assert "analysis/report.md" in files

    @pytest.mark.asyncio
    async def test_session_delete(self, session_storage, test_user_id):
        """Test deleting a session."""
        session_id = f"session_{uuid4().hex[:8]}"
        await session_storage.create_session(TENANT, session_id, test_user_id)

        # Verify exists
        info = await session_storage.get_session(TENANT, session_id)
        assert info is not None

        # Delete
        deleted = await session_storage.delete_session(TENANT, session_id)
        assert deleted is True

        # Verify gone
        with pytest.raises(ValueError):
            await session_storage.get_session(TENANT, session_id)

    @pytest.mark.asyncio
    async def test_session_path_traversal_blocked(self, session_storage, test_user_id):
        """Test that path traversal attacks are blocked."""
        session_id = f"session_{uuid4().hex[:8]}"
        await session_storage.create_session(TENANT, session_id, test_user_id)

        # Attempt path traversal
        with pytest.raises(ValueError, match="Path traversal"):
            await session_storage.write(TENANT, session_id, "../../../etc/passwd", "malicious")

    @pytest.mark.asyncio
    async def test_session_get_or_create(self, session_storage, test_user_id):
        """Test get_or_create_session functionality."""
        session_id = f"session_{uuid4().hex[:8]}"

        # First call creates
        info1 = await session_storage.get_or_create_session(TENANT, session_id, test_user_id)
        assert info1.session_id == session_id

        # Second call gets existing
        info2 = await session_storage.get_or_create_session(TENANT, session_id, test_user_id)
        assert info2.session_id == session_id
        assert info2.created_at == info1.created_at

    @pytest.mark.asyncio
    async def test_session_list_sessions(self, session_storage, test_user_id):
        """Test listing all sessions."""
        # Create multiple sessions
        session_ids = [f"session_{uuid4().hex[:8]}" for _ in range(3)]
        for sid in session_ids:
            await session_storage.create_session(TENANT, sid, test_user_id)

        sessions = await session_storage.list_sessions(TENANT)

        assert len(sessions) >= 3
        for sid in session_ids:
            assert any(s.session_id == sid for s in sessions)


# =============================================================================
# Integration: Combined Feature Tests
# =============================================================================


class TestArchitecture2Combined:
    """Test features working together."""

    @pytest.mark.asyncio
    async def test_state_machine_with_session_storage(
        self, session_storage, state_machine, test_user_id, temp_session_dir
    ):
        """Test state machine persistence with session storage."""
        session_id = f"session_{uuid4().hex[:8]}"
        await session_storage.create_session(TENANT, session_id, test_user_id)

        # Create and evolve state context
        context = StateContext(workflow_id=session_id)
        context = state_machine.transition(
            context, WorkflowState.DATA_GATHERING, reason="Start"
        )
        context = state_machine.transition(
            context, WorkflowState.ANALYSIS, reason="Data complete"
        )

        # Persist state to session
        session_path = temp_session_dir / TENANT / session_id
        await state_machine.persist_state(context, session_path)

        # Load state back
        loaded = await state_machine.load_state(session_path)

        assert loaded.current_state == WorkflowState.ANALYSIS
        assert loaded.transition_count == 2
        assert len(loaded.state_history) == 2

    @pytest.mark.asyncio
    async def test_checkpoint_with_state_context(
        self, integration_db, session_storage, state_machine, test_user_id, test_tenant_id
    ):
        """Test checkpointing state machine transitions."""
        session_id = f"session_{uuid4().hex[:8]}"
        await session_storage.create_session(TENANT, session_id, test_user_id)

        engine = DurableWorkflowEngine(
            db=integration_db,
            user_id=test_user_id,
            tenant_id=test_tenant_id,
        )

        # Stub out RLS context setting (not supported in SQLite)
        engine._ensure_rls_context = AsyncMock()

        workflow_uuid = uuid4()

        # Create checkpoint with state context
        context = StateContext(workflow_id=session_id)
        context = state_machine.transition(
            context, WorkflowState.DATA_GATHERING, reason="Start"
        )

        checkpoint = await engine._create_checkpoint(
            workflow_uuid=workflow_uuid,
            step_number=0,
            step_name="data_gathering:fetch_data",
            node_id="node_1",
            input_data={
                "state_context": context.to_dict(),
                "session_id": session_id,
            },
        )

        await engine._complete_checkpoint(
            checkpoint, {"data": "fetched", "state_context": context.to_dict()}
        )

        # Verify checkpoint contains state
        checkpoints = await engine.replay_workflow(workflow_uuid)
        assert len(checkpoints) == 1
        assert checkpoints[0]["input_data"]["state_context"]["current_state"] == "data_gathering"
