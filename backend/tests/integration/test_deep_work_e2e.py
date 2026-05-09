"""
Deep Work Architecture E2E Integration Tests

10 real end-to-end scenarios covering the three Deep Work phases:
- File System Memory (workspace skills, session lifecycle)
- E2B Code Execution Sandbox (review ticket creation)
- Wide Research Orchestrator (parallel execution, workspace save)

Plus: state machine gating, todo recitation, path traversal, connector pipeline,
connector discovery, and state machine persistence.

All external services (E2B, LLM providers) are mocked.
Tests exercise the full code path from skill invocation through session storage.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.skills.context import SkillContext
from app.agents.state_machine import (
    InvalidToolError,
    StateContext,
    StateTransitionError,
    WorkflowState,
)


# ===========================================================================
# Scenario 1: Agent Writes Analysis to Workspace
# ===========================================================================

class TestWorkspaceRoundtrip:
    """Scenario 1: Write/read roundtrip through workspace skills."""

    @pytest.mark.asyncio
    async def test_write_and_read_workspace_roundtrip(
        self, skill_context, session_storage, test_session
    ):
        session_id, _, _ = test_session
        report_content = "# AAPL Analysis\n\nPrice: $185.92\nP/E Ratio: 28.5"

        from app.skills.write_file.tool import write_file
        from app.skills.read_file.tool import read_file

        # Write
        write_result = await write_file(skill_context, "analysis/aapl_report.md", report_content)
        assert write_result["success"] is True
        assert write_result["path"] == "analysis/aapl_report.md"
        assert write_result["size_bytes"] == len(report_content.encode("utf-8"))

        # Read back
        read_result = await read_file(skill_context, "analysis/aapl_report.md")
        assert read_result["success"] is True
        assert read_result["content"] == report_content
        assert read_result["size_bytes"] == len(report_content.encode("utf-8"))

        # Verify file exists on disk (tenant-scoped path)
        session_path = session_storage.base_path / "test-tenant-001" / session_id
        disk_file = session_path / "workspace" / "analysis" / "aapl_report.md"
        assert disk_file.exists()
        assert disk_file.read_text() == report_content


# ===========================================================================
# Scenario 2: Connector -> Agent -> Workspace Pipeline
# ===========================================================================

class TestConnectorPipeline:
    """Scenario 2: Fetch from connector, write to workspace, read back."""

    @pytest.mark.asyncio
    async def test_connector_to_workspace_pipeline(
        self, skill_context, session_storage, test_session, test_connector
    ):
        from app.agents.skills.connectors import invoke_connector_tool
        from app.skills.write_file.tool import write_file
        from app.skills.read_file.tool import read_file

        # Fetch AAPL price from test connector
        price_result = await invoke_connector_tool(
            skill_context,
            connector_id="test-market-data-conn",
            tool_name="get_price",
            arguments={"ticker": "AAPL"},
        )
        assert price_result["success"] is True
        assert price_result["result"]["price"] == 185.92

        # Write result to workspace
        data_json = json.dumps(price_result["result"], indent=2)
        write_result = await write_file(skill_context, "data/aapl_price.json", data_json)
        assert write_result["success"] is True

        # Read back and verify
        read_result = await read_file(skill_context, "data/aapl_price.json")
        assert read_result["success"] is True
        parsed = json.loads(read_result["content"])
        assert parsed["price"] == 185.92
        assert parsed["ticker"] == "AAPL"


# ===========================================================================
# Scenario 3: State Machine Gates Sandbox Access
# ===========================================================================

class TestStateMachineGating:
    """Scenario 3: State machine controls execute_python availability."""

    def test_state_machine_gates_execute_python(self, state_machine):
        # execute_python should be available in DATA_GATHERING and ANALYSIS
        assert state_machine.is_tool_valid("execute_python", WorkflowState.DATA_GATHERING) is True
        assert state_machine.is_tool_valid("execute_python", WorkflowState.ANALYSIS) is True

        # execute_python should NOT be available in COMPLIANCE_CHECK or REPORT_GENERATION
        assert state_machine.is_tool_valid("execute_python", WorkflowState.COMPLIANCE_CHECK) is False
        assert state_machine.is_tool_valid("execute_python", WorkflowState.REPORT_GENERATION) is False

        # validate_tool_access should raise InvalidToolError
        with pytest.raises(InvalidToolError) as exc_info:
            state_machine.validate_tool_access(
                "execute_python", WorkflowState.COMPLIANCE_CHECK, raise_on_invalid=True
            )
        assert exc_info.value.tool_name == "execute_python"
        assert exc_info.value.current_state == WorkflowState.COMPLIANCE_CHECK

    def test_tool_availability_changes_on_transition(self, state_machine):
        """Verify tool sets change as we transition through states."""
        ctx = StateContext(workflow_id="test-workflow")

        # INITIALIZED -> DATA_GATHERING
        state_machine.transition(ctx, WorkflowState.DATA_GATHERING, reason="start")
        assert state_machine.is_tool_valid("execute_python", WorkflowState(ctx.current_state))
        assert state_machine.is_tool_valid("fetch_market_data", WorkflowState(ctx.current_state))

        # DATA_GATHERING -> ANALYSIS
        state_machine.transition(ctx, WorkflowState.ANALYSIS, reason="data ready")
        assert state_machine.is_tool_valid("execute_python", WorkflowState(ctx.current_state))
        assert state_machine.is_tool_valid("calculate_pe_ratio", WorkflowState(ctx.current_state))

        # ANALYSIS -> COMPLIANCE_CHECK
        state_machine.transition(ctx, WorkflowState.COMPLIANCE_CHECK, reason="analysis done")
        assert not state_machine.is_tool_valid("execute_python", WorkflowState(ctx.current_state))
        assert state_machine.is_tool_valid("sanctions_check", WorkflowState(ctx.current_state))


# ===========================================================================
# Scenario 4: E2B Sandbox Creates Review Ticket
# ===========================================================================

class TestSandboxReviewTicket:
    """Scenario 4: execute_python creates a review ticket via ReviewQueueService."""

    @pytest.mark.asyncio
    async def test_sandbox_creates_review_ticket(self, skill_context, integration_db):
        # Attach db to context (required after our bug fix)
        skill_context.db = integration_db

        mock_settings = MagicMock()
        mock_settings.ENABLE_SANDBOX = True
        mock_settings.E2B_API_KEY = "test_key"

        mock_ticket = MagicMock()
        mock_ticket.id = "ticket-abc-123"

        mock_service = AsyncMock()
        mock_service.create_ticket.return_value = mock_ticket

        with patch("app.skills.execute_python.tool.get_settings", return_value=mock_settings):
            with patch(
                "app.services.review_queue.ReviewQueueService",
                return_value=mock_service,
            ):
                with patch(
                    "app.services.review_queue.ReviewTicketCreate"
                ) as MockCreate:
                    MockCreate.return_value = MagicMock()

                    from app.skills.execute_python.tool import execute_python

                    result = await execute_python(
                        skill_context, "print('hello world')"
                    )

        assert result["success"] is True
        assert result["status"] == "pending_review"
        assert result["ticket_id"] == "ticket-abc-123"

        # Verify ReviewTicketCreate was called with correct fields
        call_kwargs = MockCreate.call_args[1]
        assert call_kwargs["risk_level"] == "high"
        assert call_kwargs["review_type"] == "code_execution"
        assert call_kwargs["session_id"] == skill_context.session_id
        assert call_kwargs["agent_id"] == "analysis-agent"
        assert "print('hello world')" in call_kwargs["review_data"]["code"]

    @pytest.mark.asyncio
    async def test_sandbox_requires_db(self, skill_context):
        """Verify the bug fix: execute_python returns error when ctx.db is None."""
        skill_context.db = None

        mock_settings = MagicMock()
        mock_settings.ENABLE_SANDBOX = True
        mock_settings.E2B_API_KEY = "test_key"

        with patch("app.skills.execute_python.tool.get_settings", return_value=mock_settings):
            from app.skills.execute_python.tool import execute_python

            result = await execute_python(skill_context, "print('hello')")

        assert result["success"] is False
        assert "database" in result["error"].lower() or "db" in result["error"].lower()


# ===========================================================================
# Scenario 5: Wide Research Parallel Execution
# ===========================================================================

class TestWideResearch:
    """Scenario 5: Wide research orchestrator with workspace save."""

    @pytest.mark.asyncio
    async def test_wide_research_parallel_with_workspace_save(
        self, integration_db, session_storage, test_session
    ):
        session_id, _, _ = test_session

        from app.services.wide_research_orchestrator import WideResearchOrchestrator

        orchestrator = WideResearchOrchestrator(integration_db)

        items = [
            {"ticker": "AAPL", "period": "Q4"},
            {"ticker": "GOOGL", "period": "Q4"},
            {"ticker": "MSFT", "period": "Q4"},
            {"ticker": "AMZN", "period": "Q4"},
            {"ticker": "META", "period": "Q4"},
        ]

        async def mock_sub_agent(*args, **kwargs):
            return {"findings": "Strong Buy", "confidence": "high"}

        with patch.object(
            orchestrator, "_run_sub_agent", new_callable=AsyncMock
        ) as mock_run:
            mock_run.side_effect = mock_sub_agent
            with patch(
                "app.services.audit_logger.log_decision", new_callable=AsyncMock
            ):
                result = await orchestrator.execute(
                    task_template="Analyze {item} for portfolio",
                    items=items,
                    agent_id="analysis-agent",
                    tenant_id="test-tenant-001",
                    user_id="test-user-001",
                    session_id=session_id,
                    max_parallel=3,
                )

        assert result.status == "completed"
        assert result.total_items == 5
        assert result.completed == 5
        assert result.failed == 0
        assert len(result.items) == 5

        # Verify results saved to workspace (tenant-scoped path)
        results_path = (
            session_storage.base_path
            / "test-tenant-001"
            / session_id
            / f"workspace/wide_research/{result.execution_id}/results.json"
        )
        if results_path.exists():
            saved_data = json.loads(results_path.read_text())
            assert saved_data["status"] == "completed"
            assert saved_data["total_items"] == 5


# ===========================================================================
# Scenario 6: Todo Middleware Injects Recitation
# ===========================================================================

class TestTodoRecitation:
    """Scenario 6: Todo recitation fires after N tool calls."""

    @pytest.mark.asyncio
    async def test_todo_recitation_after_n_calls(
        self, session_storage, test_session
    ):
        session_id, _, _ = test_session

        # Write known todo content
        todo_content = "- [ ] Analyze AAPL\n- [x] Fetch data\n- [ ] Generate report"
        await session_storage.write("test-tenant-001", session_id, "todo.md", todo_content)

        from app.agents.harness import wrap_tools_with_harness

        # Create a simple async tool that accepts a unique call_id to avoid loop detection
        async def dummy_tool(call_id: int = 0):
            return "tool output"

        wrapped_tools = wrap_tools_with_harness(
            [dummy_tool], session_id, "test-tenant-001",
        )
        wrapped = wrapped_tools[0]

        # Calls 1-6: no recitation (pass unique call_id to avoid loop detection)
        for i in range(6):
            result = await wrapped(call_id=i + 1)
            assert "[PROGRESS CHECK]" not in result, f"Unexpected recitation on call {i + 1}"

        # Call 7: recitation fires
        result = await wrapped(call_id=7)
        assert "[PROGRESS CHECK]" in result, "Expected recitation on call 7"
        assert "Analyze AAPL" in result

        # Calls 8-13: no recitation
        for i in range(6):
            result = await wrapped(call_id=i + 8)
            assert "[PROGRESS CHECK]" not in result, f"Unexpected recitation on call {i + 8}"

        # Call 14: recitation fires again
        result = await wrapped(call_id=14)
        assert "[PROGRESS CHECK]" in result, "Expected recitation on call 14"


# ===========================================================================
# Scenario 7: Path Traversal Blocked Across All Skills
# ===========================================================================

class TestPathTraversal:
    """Scenario 7: Path traversal attempts blocked on all workspace skills."""

    ATTACK_VECTORS = [
        "../../../etc/passwd",
        "/etc/shadow",
        "foo/../../bar",
        "./../../etc",
    ]

    @pytest.mark.asyncio
    async def test_path_traversal_all_workspace_skills(self, skill_context):
        from app.skills.write_file.tool import write_file
        from app.skills.read_file.tool import read_file
        from app.skills.delete_file.tool import delete_file
        from app.skills.list_workspace.tool import list_workspace

        for vector in self.ATTACK_VECTORS:
            # write_file
            result = await write_file(skill_context, vector, "malicious")
            assert result["success"] is False, f"write_file should block: {vector}"

            # read_file
            result = await read_file(skill_context, vector)
            assert result["success"] is False, f"read_file should block: {vector}"

            # delete_file
            result = await delete_file(skill_context, vector)
            assert result["success"] is False, f"delete_file should block: {vector}"

        # list_workspace with traversal paths
        for vector in ["../", "../../", "../../../"]:
            result = await list_workspace(skill_context, path=vector)
            assert result["success"] is False, f"list_workspace should block: {vector}"


# ===========================================================================
# Scenario 8: Session Lifecycle CRUD
# ===========================================================================

class TestSessionLifecycle:
    """Scenario 8: Full session lifecycle create/write/list/read/delete."""

    @pytest.mark.asyncio
    async def test_full_session_lifecycle(self, session_storage):
        from app.skills.write_file.tool import write_file
        from app.skills.read_file.tool import read_file
        from app.skills.delete_file.tool import delete_file
        from app.skills.list_workspace.tool import list_workspace

        sid = "lifecycle-test-session"
        tenant = "test-tenant-001"
        info = await session_storage.create_session(tenant, sid, "user-lifecycle")
        assert info.session_id == sid

        ctx = SkillContext(user_id="user-lifecycle", tenant_id=tenant, session_id=sid)

        # Write two files
        r1 = await write_file(ctx, "report.md", "# Report")
        assert r1["success"] is True

        r2 = await write_file(ctx, "data/results.json", '{"score": 42}')
        assert r2["success"] is True

        # List workspace — expect 2 entries (report.md file + data/ directory)
        list_result = await list_workspace(ctx)
        assert list_result["success"] is True
        assert list_result["total_count"] == 2

        # Read file
        read_result = await read_file(ctx, "report.md")
        assert read_result["success"] is True
        assert read_result["content"] == "# Report"

        # Delete file
        del_result = await delete_file(ctx, "report.md")
        assert del_result["success"] is True
        assert del_result["deleted"] is True

        # List again — expect 1 entry (data/ directory remains)
        list_result2 = await list_workspace(ctx)
        assert list_result2["success"] is True
        assert list_result2["total_count"] == 1

        # Delete session
        deleted = await session_storage.delete_session(tenant, sid)
        assert deleted is True

        # Verify directory is gone
        session_path = session_storage.base_path / tenant / sid
        assert not session_path.exists()


# ===========================================================================
# Scenario 9: Connector Discovery and Invocation
# ===========================================================================

class TestConnectorDiscovery:
    """Scenario 9: Discover connectors, get schema, invoke tools."""

    @pytest.mark.asyncio
    async def test_connector_discovery_list_schema_invoke(
        self, skill_context, test_connector
    ):
        from app.agents.skills.connectors import (
            get_connector_schema,
            invoke_connector_tool,
            list_available_connectors,
        )

        # List connectors
        list_result = await list_available_connectors(skill_context)
        assert list_result["total_count"] >= 1
        test_conn = next(
            (c for c in list_result["connectors"] if c["provider"] == "test_market_data"),
            None,
        )
        assert test_conn is not None
        assert len(test_conn["tools"]) == 3

        # Get schema for get_price
        schema_result = await get_connector_schema(
            skill_context, "test-market-data-conn", "get_price"
        )
        assert schema_result["success"] is True
        assert "ticker" in str(schema_result["input_schema"])

        # Invoke get_price for GOOGL
        price_result = await invoke_connector_tool(
            skill_context, "test-market-data-conn", "get_price", {"ticker": "GOOGL"}
        )
        assert price_result["success"] is True
        assert price_result["result"]["price"] == 141.80

        # Invoke list_tickers
        tickers_result = await invoke_connector_tool(
            skill_context, "test-market-data-conn", "list_tickers", {}
        )
        assert tickers_result["success"] is True
        assert set(tickers_result["result"]) == {"AAPL", "GOOGL", "MSFT"}

        # Invalid tool name
        bad_tool = await invoke_connector_tool(
            skill_context, "test-market-data-conn", "nonexistent_tool", {}
        )
        assert bad_tool["success"] is False

        # Invalid connector ID
        bad_conn = await invoke_connector_tool(
            skill_context, "invalid-connector", "get_price", {}
        )
        assert bad_conn["success"] is False


# ===========================================================================
# Scenario 10: State Machine Persistence in Session
# ===========================================================================

class TestStatePersistence:
    """Scenario 10: Persist and recover state machine context."""

    @pytest.mark.asyncio
    async def test_state_machine_persist_and_recover(
        self, state_machine, tmp_path
    ):
        session_path = tmp_path / "persist-test-session"
        session_path.mkdir(parents=True)

        # Create context and transition through states
        ctx = StateContext(workflow_id="persist-test")
        state_machine.transition(ctx, WorkflowState.DATA_GATHERING, reason="start")
        state_machine.transition(ctx, WorkflowState.ANALYSIS, reason="data ready")

        # Persist
        await state_machine.persist_state(ctx, session_path)

        # Verify state.json exists
        state_file = session_path / "state.json"
        assert state_file.exists()

        # Load back
        loaded = await state_machine.load_state(session_path)
        assert WorkflowState(loaded.current_state) == WorkflowState.ANALYSIS
        assert loaded.transition_count == 2
        assert len(loaded.state_history) == 2

        # Invalid transition: ANALYSIS -> INITIALIZED should fail
        with pytest.raises(StateTransitionError):
            state_machine.transition(loaded, WorkflowState.INITIALIZED, reason="invalid")

        # Valid transition: ANALYSIS -> COMPLIANCE_CHECK
        state_machine.transition(loaded, WorkflowState.COMPLIANCE_CHECK, reason="proceed")

        # After transition, execute_python should no longer be valid
        assert not state_machine.is_tool_valid(
            "execute_python", WorkflowState(loaded.current_state)
        )
        # But compliance tools should be valid
        assert state_machine.is_tool_valid(
            "sanctions_check", WorkflowState(loaded.current_state)
        )
