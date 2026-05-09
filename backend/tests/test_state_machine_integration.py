"""
Tests for C2 fix: State Machine Integration

Verifies that:
- Workflow orchestrator initializes state when USE_STATE_MACHINE=True
- Node types are correctly mapped to workflow states
- LLM executor receives and processes workflow state
- State context is properly added to prompts
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from uuid import uuid4

from app.agents.state_machine import WorkflowState, STATE_TOOLS


class TestWorkflowOrchestratorStateIntegration:
    """Test that WorkflowOrchestrator integrates state machine correctly."""

    @pytest.fixture
    def mock_db(self):
        """Mock database session."""
        return AsyncMock()

    @pytest.fixture
    def basic_workflow_spec(self):
        """Basic workflow with start, LLM, and output nodes."""
        return {
            "nodes": [
                {"id": "1", "type": "start", "data": {}, "position": {"x": 0, "y": 0}},
                {"id": "2", "type": "llm", "data": {"model": "gpt-4o-mini"}, "position": {"x": 200, "y": 0}},
                {"id": "3", "type": "output", "data": {}, "position": {"x": 400, "y": 0}}
            ],
            "edges": [
                {"id": "e1", "source": "1", "target": "2"},
                {"id": "e2", "source": "2", "target": "3"}
            ]
        }

    @pytest.mark.asyncio
    async def test_state_machine_initialized_when_enabled(self, mock_db, basic_workflow_spec):
        """Verify state machine variables are set when USE_STATE_MACHINE=True."""
        from app.services.workflow_orchestrator import WorkflowOrchestrator

        with patch('app.services.workflow_orchestrator.settings') as mock_settings:
            mock_settings.USE_STATE_MACHINE = True
            mock_settings.USE_DATA_CLASSIFICATION = False

            orchestrator = WorkflowOrchestrator(
                db=mock_db,
                user_id=str(uuid4()),
                tenant_id=uuid4()
            )

            # Mock the executor to capture variables
            captured_variables = {}

            async def capture_execute(context, db):
                captured_variables.update(context.variables)
                return MagicMock(
                    status="success",
                    output={"text": "test"},
                    elapsed_ms=100
                )

            with patch('app.services.workflow_orchestrator.get_executor') as mock_get_executor:
                mock_executor = MagicMock()
                mock_executor.execute = capture_execute
                mock_get_executor.return_value = mock_executor

                await orchestrator.execute_workflow(
                    workflow_spec=basic_workflow_spec,
                    input_variables={"query": "test query"},
                    save_logs=False
                )

            # Verify state machine variables were set
            assert "sys.workflow_state" in captured_variables
            assert "sys.session_id" in captured_variables

    @pytest.mark.asyncio
    async def test_state_not_initialized_when_disabled(self, mock_db, basic_workflow_spec):
        """Verify state machine variables are NOT set when USE_STATE_MACHINE=False."""
        from app.services.workflow_orchestrator import WorkflowOrchestrator

        with patch('app.services.workflow_orchestrator.settings') as mock_settings:
            mock_settings.USE_STATE_MACHINE = False

            orchestrator = WorkflowOrchestrator(
                db=mock_db,
                user_id=str(uuid4()),
                tenant_id=uuid4()
            )

            captured_variables = {}

            async def capture_execute(context, db):
                captured_variables.update(context.variables)
                return MagicMock(
                    status="success",
                    output={"text": "test"},
                    elapsed_ms=100
                )

            with patch('app.services.workflow_orchestrator.get_executor') as mock_get_executor:
                mock_executor = MagicMock()
                mock_executor.execute = capture_execute
                mock_get_executor.return_value = mock_executor

                await orchestrator.execute_workflow(
                    workflow_spec=basic_workflow_spec,
                    input_variables={"query": "test query"},
                    save_logs=False
                )

            # Verify state machine variables were NOT set
            assert "sys.workflow_state" not in captured_variables


class TestNodeStateMapping:
    """Test that node types map correctly to workflow states."""

    def test_start_node_maps_to_initialized(self):
        """Verify start node maps to INITIALIZED state."""
        from app.services.workflow_orchestrator import WorkflowOrchestrator

        orchestrator = WorkflowOrchestrator(
            db=AsyncMock(),
            user_id=str(uuid4())
        )

        node = {"type": "start", "data": {}}
        state = orchestrator._infer_state_from_node(node)

        assert state == WorkflowState.INITIALIZED

    def test_llm_node_maps_to_analysis(self):
        """Verify LLM node maps to ANALYSIS state."""
        from app.services.workflow_orchestrator import WorkflowOrchestrator

        orchestrator = WorkflowOrchestrator(
            db=AsyncMock(),
            user_id=str(uuid4())
        )

        node = {"type": "llm", "data": {}}
        state = orchestrator._infer_state_from_node(node)

        assert state == WorkflowState.ANALYSIS

    def test_data_retrieval_node_maps_to_data_gathering(self):
        """Verify data retrieval node maps to DATA_GATHERING state."""
        from app.services.workflow_orchestrator import WorkflowOrchestrator

        orchestrator = WorkflowOrchestrator(
            db=AsyncMock(),
            user_id=str(uuid4())
        )

        node = {"type": "data_retrieval", "data": {}}
        state = orchestrator._infer_state_from_node(node)

        assert state == WorkflowState.DATA_GATHERING

    def test_compliance_node_maps_to_compliance_check(self):
        """Verify compliance node maps to COMPLIANCE_CHECK state."""
        from app.services.workflow_orchestrator import WorkflowOrchestrator

        orchestrator = WorkflowOrchestrator(
            db=AsyncMock(),
            user_id=str(uuid4())
        )

        node = {"type": "compliance", "data": {}}
        state = orchestrator._infer_state_from_node(node)

        assert state == WorkflowState.COMPLIANCE_CHECK

    def test_output_node_maps_to_report_generation(self):
        """Verify output node maps to REPORT_GENERATION state."""
        from app.services.workflow_orchestrator import WorkflowOrchestrator

        orchestrator = WorkflowOrchestrator(
            db=AsyncMock(),
            user_id=str(uuid4())
        )

        node = {"type": "output", "data": {}}
        state = orchestrator._infer_state_from_node(node)

        assert state == WorkflowState.REPORT_GENERATION

    def test_unknown_node_defaults_to_analysis(self):
        """Verify unknown node types default to ANALYSIS state."""
        from app.services.workflow_orchestrator import WorkflowOrchestrator

        orchestrator = WorkflowOrchestrator(
            db=AsyncMock(),
            user_id=str(uuid4())
        )

        node = {"type": "unknown_custom_type", "data": {}}
        state = orchestrator._infer_state_from_node(node)

        assert state == WorkflowState.ANALYSIS


class TestLLMExecutorStateIntegration:
    """Test that LLM executor properly handles workflow state."""

    def test_get_workflow_state_from_context(self):
        """Verify workflow state extraction from context."""
        from app.node_executors.llm_executor import LLMExecutor
        from app.node_executors import NodeExecutionContext

        executor = LLMExecutor()

        context = NodeExecutionContext(
            node_id="test",
            node_type="llm",
            node_data={},
            inputs={},
            variables={"sys.workflow_state": "analysis"},
            user_id=str(uuid4())
        )

        state = executor._get_workflow_state_from_context(context)

        assert state == WorkflowState.ANALYSIS

    def test_get_workflow_state_returns_none_when_missing(self):
        """Verify None is returned when state not in context."""
        from app.node_executors.llm_executor import LLMExecutor
        from app.node_executors import NodeExecutionContext

        executor = LLMExecutor()

        context = NodeExecutionContext(
            node_id="test",
            node_type="llm",
            node_data={},
            inputs={},
            variables={},
            user_id=str(uuid4())
        )

        state = executor._get_workflow_state_from_context(context)

        assert state is None

    def test_add_state_context_to_prompt(self):
        """Verify state context is properly added to prompts."""
        from app.node_executors.llm_executor import LLMExecutor

        executor = LLMExecutor()

        original_prompt = "Analyze this data."
        enhanced_prompt = executor._add_state_context_to_prompt(
            original_prompt,
            WorkflowState.ANALYSIS
        )

        # Verify state info is added
        assert "[Workflow State: analysis]" in enhanced_prompt
        assert "[Available Tools:" in enhanced_prompt
        assert original_prompt in enhanced_prompt


class TestStateToolsMapping:
    """Test that state-to-tools mapping is correct."""

    def test_data_gathering_has_market_data_tools(self):
        """Verify DATA_GATHERING state has data retrieval tools."""
        tools = STATE_TOOLS[WorkflowState.DATA_GATHERING]

        # Check for expected tools
        assert "fetch_market_data" in tools
        assert "get_financial_statements" in tools
        assert "read_volume_file" in tools

    def test_analysis_has_calculation_tools(self):
        """Verify ANALYSIS state has calculation tools."""
        tools = STATE_TOOLS[WorkflowState.ANALYSIS]

        # Check for expected tools
        assert "calculate_pe_ratio" in tools
        assert "calculate_debt_to_equity" in tools

    def test_compliance_has_screening_tools(self):
        """Verify COMPLIANCE_CHECK state has screening tools."""
        tools = STATE_TOOLS[WorkflowState.COMPLIANCE_CHECK]

        # Check for expected tools
        assert "kyc_verification" in tools
        assert "sanctions_check" in tools

    def test_completed_state_has_limited_tools(self):
        """Verify COMPLETED state has very limited tools."""
        tools = STATE_TOOLS[WorkflowState.COMPLETED]

        # Should only have read-only tools
        assert "read_artifact" in tools
        assert len(tools) < 10  # Should be minimal

    def test_states_dont_have_invalid_tools(self):
        """Verify states don't grant access to inappropriate tools."""
        # COMPLETED should not have write tools
        completed_tools = STATE_TOOLS[WorkflowState.COMPLETED]
        assert "save_artifact" not in completed_tools
        assert "save_compliance_report" not in completed_tools

        # DATA_GATHERING should not have compliance tools
        data_tools = STATE_TOOLS[WorkflowState.DATA_GATHERING]
        assert "check_insider_trading_risk" not in data_tools

