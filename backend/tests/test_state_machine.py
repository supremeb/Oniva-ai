"""
Tests for Phase 3 State Machine and Compliance components.

Tests cover:
- WorkflowState enum and transitions
- Tool access validation
- StateMachineManager functionality
- StateContext persistence
- StatefulAgent wrapper
"""

import pytest

from app.agents.state_machine import (
    WorkflowState,
    StateContext,
    StateMachineManager,
    StateTransitionError,
    InvalidToolError,
    STATE_TOOLS,
    STATE_TRANSITIONS,
    get_state_machine_manager,
    is_tool_valid_for_state,
    get_valid_tools_for_state,
    is_terminal_state,
    get_terminal_states,
)


# =============================================================================
# WorkflowState Tests
# =============================================================================

class TestWorkflowState:
    """Tests for WorkflowState enum."""

    def test_all_states_defined(self):
        """Verify all expected states are defined."""
        expected_states = {
            'initialized',
            'data_gathering',
            'analysis',
            'compliance_check',
            'human_review',
            'report_generation',
            'completed',
            'failed',
        }
        actual_states = {s.value for s in WorkflowState}
        assert actual_states == expected_states

    def test_state_string_conversion(self):
        """Verify state can be converted to/from string."""
        state = WorkflowState.ANALYSIS
        assert state.value == 'analysis'
        assert WorkflowState('analysis') == WorkflowState.ANALYSIS


# =============================================================================
# STATE_TOOLS Tests
# =============================================================================

class TestStateTools:
    """Tests for STATE_TOOLS mapping."""

    def test_all_states_have_tools(self):
        """Verify every state has tools defined."""
        for state in WorkflowState:
            assert state in STATE_TOOLS, f"State {state} missing from STATE_TOOLS"
            assert isinstance(STATE_TOOLS[state], set)

    def test_data_gathering_has_market_tools(self):
        """Verify DATA_GATHERING state has data retrieval tools."""
        tools = STATE_TOOLS[WorkflowState.DATA_GATHERING]
        assert 'fetch_market_data' in tools
        assert 'get_financial_statements' in tools
        assert 'read_volume_file' in tools

    def test_analysis_has_calculation_tools(self):
        """Verify ANALYSIS state has calculation tools."""
        tools = STATE_TOOLS[WorkflowState.ANALYSIS]
        assert 'calculate_pe_ratio' in tools
        assert 'calculate_debt_to_equity' in tools
        assert 'calculate_roe' in tools

    def test_compliance_has_screening_tools(self):
        """Verify COMPLIANCE_CHECK state has screening tools."""
        tools = STATE_TOOLS[WorkflowState.COMPLIANCE_CHECK]
        assert 'kyc_verification' in tools
        assert 'aml_screening' in tools
        assert 'sanctions_check' in tools

    def test_human_review_limited_tools(self):
        """Verify HUMAN_REVIEW has only read tools (plus planning)."""
        tools = STATE_TOOLS[WorkflowState.HUMAN_REVIEW]
        assert 'read_artifact' in tools
        assert 'fetch_market_data' not in tools  # No data gathering
        assert 'calculate_pe_ratio' not in tools  # No analysis

    def test_terminal_states_minimal_tools(self):
        """Verify terminal states have minimal tool access."""
        completed_tools = STATE_TOOLS[WorkflowState.COMPLETED]
        failed_tools = STATE_TOOLS[WorkflowState.FAILED]

        # Should only have read tools (plus planning skills)
        assert 'read_artifact' in completed_tools
        assert 'fetch_market_data' not in completed_tools
        assert 'read_plan' in failed_tools


# =============================================================================
# STATE_TRANSITIONS Tests
# =============================================================================

class TestStateTransitions:
    """Tests for STATE_TRANSITIONS mapping."""

    def test_all_states_have_transitions(self):
        """Verify every state has transitions defined."""
        for state in WorkflowState:
            assert state in STATE_TRANSITIONS

    def test_initialized_can_only_go_to_data_gathering_or_fail(self):
        """Verify INITIALIZED has correct transitions."""
        transitions = STATE_TRANSITIONS[WorkflowState.INITIALIZED]
        assert WorkflowState.DATA_GATHERING in transitions
        assert WorkflowState.FAILED in transitions
        assert len(transitions) == 2

    def test_terminal_states_have_no_transitions(self):
        """Verify terminal states cannot transition."""
        assert STATE_TRANSITIONS[WorkflowState.COMPLETED] == set()
        assert STATE_TRANSITIONS[WorkflowState.FAILED] == set()

    def test_workflow_path_exists(self):
        """Verify a complete workflow path is possible."""
        # INITIALIZED -> DATA_GATHERING -> ANALYSIS -> COMPLIANCE_CHECK -> REPORT_GENERATION -> COMPLETED
        assert WorkflowState.DATA_GATHERING in STATE_TRANSITIONS[WorkflowState.INITIALIZED]
        assert WorkflowState.ANALYSIS in STATE_TRANSITIONS[WorkflowState.DATA_GATHERING]
        assert WorkflowState.COMPLIANCE_CHECK in STATE_TRANSITIONS[WorkflowState.ANALYSIS]
        assert WorkflowState.REPORT_GENERATION in STATE_TRANSITIONS[WorkflowState.COMPLIANCE_CHECK]
        assert WorkflowState.COMPLETED in STATE_TRANSITIONS[WorkflowState.REPORT_GENERATION]


# =============================================================================
# StateContext Tests
# =============================================================================

class TestStateContext:
    """Tests for StateContext model."""

    def test_default_values(self):
        """Verify StateContext has correct defaults."""
        context = StateContext()
        assert context.current_state == WorkflowState.INITIALIZED
        assert context.previous_state is None
        assert context.state_history == []
        assert context.transition_count == 0

    def test_to_dict_serialization(self):
        """Verify StateContext can be serialized to dict."""
        context = StateContext(
            current_state=WorkflowState.ANALYSIS,
            workflow_id='test-123',
        )
        data = context.to_dict()
        assert data['current_state'] == 'analysis'
        assert data['workflow_id'] == 'test-123'
        assert 'started_at' in data
        assert 'updated_at' in data

    def test_from_dict_deserialization(self):
        """Verify StateContext can be deserialized from dict."""
        data = {
            'current_state': 'data_gathering',
            'workflow_id': 'test-456',
            'transition_count': 3,
        }
        context = StateContext.from_dict(data)
        assert context.current_state == WorkflowState.DATA_GATHERING
        assert context.workflow_id == 'test-456'
        assert context.transition_count == 3


# =============================================================================
# StateMachineManager Tests
# =============================================================================

class TestStateMachineManager:
    """Tests for StateMachineManager class."""

    @pytest.fixture
    def manager(self):
        """Get a fresh manager instance."""
        return StateMachineManager()

    def test_get_valid_tools(self, manager):
        """Verify get_valid_tools returns correct tools."""
        tools = manager.get_valid_tools(WorkflowState.DATA_GATHERING)
        assert 'fetch_market_data' in tools
        assert isinstance(tools, set)

    def test_is_tool_valid_true(self, manager):
        """Verify is_tool_valid returns True for valid tools."""
        assert manager.is_tool_valid('fetch_market_data', WorkflowState.DATA_GATHERING)
        assert manager.is_tool_valid('calculate_pe_ratio', WorkflowState.ANALYSIS)

    def test_is_tool_valid_false(self, manager):
        """Verify is_tool_valid returns False for invalid tools."""
        assert not manager.is_tool_valid('fetch_market_data', WorkflowState.ANALYSIS)
        assert not manager.is_tool_valid('calculate_pe_ratio', WorkflowState.DATA_GATHERING)

    def test_validate_tool_access_raises(self, manager):
        """Verify validate_tool_access raises InvalidToolError."""
        with pytest.raises(InvalidToolError) as exc_info:
            manager.validate_tool_access(
                'fetch_market_data',
                WorkflowState.ANALYSIS,
            )
        assert exc_info.value.tool_name == 'fetch_market_data'
        assert exc_info.value.current_state == WorkflowState.ANALYSIS

    def test_validate_tool_access_no_raise(self, manager):
        """Verify validate_tool_access can return False instead of raising."""
        result = manager.validate_tool_access(
            'fetch_market_data',
            WorkflowState.ANALYSIS,
            raise_on_invalid=False,
        )
        assert result is False

    def test_get_valid_transitions(self, manager):
        """Verify get_valid_transitions returns correct transitions."""
        transitions = manager.get_valid_transitions(WorkflowState.DATA_GATHERING)
        assert WorkflowState.ANALYSIS in transitions
        assert WorkflowState.COMPLIANCE_CHECK in transitions

    def test_validate_transition_raises(self, manager):
        """Verify validate_transition raises StateTransitionError."""
        with pytest.raises(StateTransitionError) as exc_info:
            manager.validate_transition(
                WorkflowState.DATA_GATHERING,
                WorkflowState.COMPLETED,  # Invalid - must go through other states
            )
        assert exc_info.value.current_state == WorkflowState.DATA_GATHERING
        assert exc_info.value.requested_state == WorkflowState.COMPLETED

    def test_transition_success(self, manager):
        """Verify successful state transition."""
        context = StateContext(current_state=WorkflowState.INITIALIZED)

        updated = manager.transition(
            context,
            WorkflowState.DATA_GATHERING,
            reason='Starting data collection',
        )

        assert updated.current_state == WorkflowState.DATA_GATHERING
        assert updated.previous_state == WorkflowState.INITIALIZED
        assert updated.transition_count == 1
        assert len(updated.state_history) == 1
        assert updated.state_history[0]['from'] == 'initialized'
        assert updated.state_history[0]['to'] == 'data_gathering'

    def test_transition_failure(self, manager):
        """Verify failed state transition raises error."""
        context = StateContext(current_state=WorkflowState.INITIALIZED)

        with pytest.raises(StateTransitionError):
            manager.transition(
                context,
                WorkflowState.COMPLETED,  # Invalid from INITIALIZED
            )


# =============================================================================
# Singleton and Utility Function Tests
# =============================================================================

class TestUtilityFunctions:
    """Tests for utility functions."""

    def test_get_state_machine_manager_singleton(self):
        """Verify singleton pattern."""
        manager1 = get_state_machine_manager()
        manager2 = get_state_machine_manager()
        # Should be the same instance
        assert manager1 is manager2

    def test_get_valid_tools_for_state(self):
        """Verify convenience function."""
        tools = get_valid_tools_for_state(WorkflowState.ANALYSIS)
        assert 'calculate_pe_ratio' in tools

    def test_is_tool_valid_for_state(self):
        """Verify convenience function."""
        assert is_tool_valid_for_state('fetch_market_data', WorkflowState.DATA_GATHERING)
        assert not is_tool_valid_for_state('fetch_market_data', WorkflowState.ANALYSIS)

    def test_get_terminal_states(self):
        """Verify terminal states."""
        terminal = get_terminal_states()
        assert WorkflowState.COMPLETED in terminal
        assert WorkflowState.FAILED in terminal
        assert len(terminal) == 2

    def test_is_terminal_state(self):
        """Verify is_terminal_state function."""
        assert is_terminal_state(WorkflowState.COMPLETED)
        assert is_terminal_state(WorkflowState.FAILED)
        assert not is_terminal_state(WorkflowState.ANALYSIS)


# =============================================================================
# Security Tests
# =============================================================================

class TestSecurityGuarantees:
    """Tests for security guarantees of the state machine."""

    def test_no_state_can_access_all_tools(self):
        """Verify no single state has access to all possible tools."""
        all_tools = set()
        for tools in STATE_TOOLS.values():
            all_tools.update(tools)

        for state in WorkflowState:
            state_tools = STATE_TOOLS[state]
            assert state_tools != all_tools, f"State {state} has access to all tools"

    def test_dangerous_tools_restricted(self):
        """Verify sensitive tools are not universally available."""
        sensitive_tools = {
            'check_insider_trading_risk',
            'sanctions_check',
            'aml_screening',
        }

        for tool in sensitive_tools:
            states_with_tool = [
                state for state in WorkflowState
                if tool in STATE_TOOLS[state]
            ]
            # Should not be in more than 2 states
            assert len(states_with_tool) <= 2, f"Tool {tool} too widely available"

    def test_terminal_states_read_only(self):
        """Verify terminal states only have read-only tools."""
        write_tools = {
            'save_artifact',
            'save_compliance_report',
            'save_analysis_report',
            'fetch_market_data',  # Also considered write as it makes external calls
        }

        terminal_tools = (
            STATE_TOOLS[WorkflowState.COMPLETED] |
            STATE_TOOLS[WorkflowState.FAILED]
        )

        for tool in write_tools:
            assert tool not in terminal_tools, f"Write tool {tool} in terminal state"


# =============================================================================
# Integration Tests
# =============================================================================

class TestWorkflowIntegration:
    """Integration tests for complete workflow scenarios."""

    @pytest.fixture
    def manager(self):
        return StateMachineManager()

    def test_complete_workflow_path(self, manager):
        """Test a complete successful workflow path."""
        context = StateContext()

        # INITIALIZED -> DATA_GATHERING
        context = manager.transition(context, WorkflowState.DATA_GATHERING)
        assert context.current_state == WorkflowState.DATA_GATHERING

        # DATA_GATHERING -> ANALYSIS
        context = manager.transition(context, WorkflowState.ANALYSIS)
        assert context.current_state == WorkflowState.ANALYSIS

        # ANALYSIS -> COMPLIANCE_CHECK
        context = manager.transition(context, WorkflowState.COMPLIANCE_CHECK)
        assert context.current_state == WorkflowState.COMPLIANCE_CHECK

        # COMPLIANCE_CHECK -> REPORT_GENERATION
        context = manager.transition(context, WorkflowState.REPORT_GENERATION)
        assert context.current_state == WorkflowState.REPORT_GENERATION

        # REPORT_GENERATION -> COMPLETED
        context = manager.transition(context, WorkflowState.COMPLETED)
        assert context.current_state == WorkflowState.COMPLETED

        # Verify history
        assert context.transition_count == 5
        assert len(context.state_history) == 5

    def test_workflow_with_human_review(self, manager):
        """Test workflow path that includes human review."""
        context = StateContext()

        # Progress to compliance check
        context = manager.transition(context, WorkflowState.DATA_GATHERING)
        context = manager.transition(context, WorkflowState.ANALYSIS)
        context = manager.transition(context, WorkflowState.COMPLIANCE_CHECK)

        # Go to human review
        context = manager.transition(context, WorkflowState.HUMAN_REVIEW)
        assert context.current_state == WorkflowState.HUMAN_REVIEW

        # After review, go to report generation
        context = manager.transition(context, WorkflowState.REPORT_GENERATION)
        assert context.current_state == WorkflowState.REPORT_GENERATION

    def test_workflow_failure_at_any_state(self, manager):
        """Test that any non-terminal state can transition to FAILED."""
        non_terminal_states = [
            WorkflowState.INITIALIZED,
            WorkflowState.DATA_GATHERING,
            WorkflowState.ANALYSIS,
            WorkflowState.COMPLIANCE_CHECK,
            WorkflowState.HUMAN_REVIEW,
            WorkflowState.REPORT_GENERATION,
        ]

        for state in non_terminal_states:
            context = StateContext(current_state=state)
            context = manager.transition(context, WorkflowState.FAILED)
            assert context.current_state == WorkflowState.FAILED
