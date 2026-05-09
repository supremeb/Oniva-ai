"""
State Machine for Agent Tool Enforcement

Agents can ONLY access tools valid for their current workflow state.

Based on Section 3.4 and StateFlow research (arXiv 2505.23643).

Key concepts:
- WorkflowState: Defines valid states in an agent workflow
- STATE_TOOLS: Maps each state to its allowed tools
- STATE_TRANSITIONS: Defines valid transitions between states
- StatefulAgent: Wrapper that enforces state machine constraints

Note: STATE_TOOLS is a static mapping while available tools are dynamic
per-request (skills + connectors are wired at runtime). This provides
structural enforcement for known tools but cannot cover dynamically
added tools. For full coverage, tools should be tagged with categories.
"""

from enum import Enum
from typing import Set, Dict, Optional, List, Any
from datetime import datetime
from pydantic import BaseModel, Field
from pathlib import Path
import json
import logging

logger = logging.getLogger(__name__)


class WorkflowState(str, Enum):
    """
    Valid states in an agent workflow.

    Each state represents a phase of work with specific tool access rights.
    """
    INITIALIZED = "initialized"
    DATA_GATHERING = "data_gathering"
    ANALYSIS = "analysis"
    COMPLIANCE_CHECK = "compliance_check"
    HUMAN_REVIEW = "human_review"
    REPORT_GENERATION = "report_generation"
    COMPLETED = "completed"
    FAILED = "failed"


class StateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(
        self,
        current_state: WorkflowState,
        requested_state: WorkflowState,
        valid_transitions: Set[WorkflowState],
    ):
        self.current_state = current_state
        self.requested_state = requested_state
        self.valid_transitions = valid_transitions
        super().__init__(
            f"Invalid state transition from {current_state.value} to {requested_state.value}. "
            f"Valid transitions: {[s.value for s in valid_transitions]}"
        )


class InvalidToolError(Exception):
    """Raised when a tool is called that's not valid for the current state."""

    def __init__(
        self,
        tool_name: str,
        current_state: WorkflowState,
        valid_tools: Set[str],
    ):
        self.tool_name = tool_name
        self.current_state = current_state
        self.valid_tools = valid_tools
        super().__init__(
            f"Tool '{tool_name}' is not valid in state {current_state.value}. "
            f"Valid tools: {list(valid_tools)[:5]}..."
        )


# =============================================================================
# Tool Mappings per State
# =============================================================================

# Common tools available in multiple states
COMMON_READ_TOOLS = {
    "read_plan",
    "read_todo",
    "read_session_state",
    "read_file",
    "list_workspace",
}

COMMON_WRITE_TOOLS = {
    "update_todo",
    "log_action",
    "write_file",
}

ARTIFACT_TOOLS = {
    "save_artifact",
    "read_artifact",
    "list_artifacts",
}

# Planning skills allowed in ALL states (Deep Work / autonomous execution)
PLANNING_SKILLS = {"present_plan", "update_todo", "ask_user", "complete_task"}

# Define valid tools for each state (defense in depth)
# These map to actual registered agent tools and skills from the registry.
STATE_TOOLS: Dict[WorkflowState, Set[str]] = {
    WorkflowState.INITIALIZED: {
        "read_plan",
        "read_session_state",
        *COMMON_READ_TOOLS,
    },

    WorkflowState.DATA_GATHERING: {
        # Agent tools (DataRetrievalAgent, ResearchAgent)
        "read_volume_file",
        "read_from_volume",
        "fetch_from_connector",
        "fetch_from_rest_api",
        "search_documents",
        "get_document_by_id",
        # Skills (registry)
        "web_search",
        "browse_url",
        "extract_structured_data",
        "fetch_market_data",
        "get_financial_statements",
        "analyze_risk_metrics",
        "execute_python",
        "parse_document",
        # Connector skills
        "list_available_connectors",
        "invoke_connector_tool",
        "get_connector_schema",
        # Storage
        *ARTIFACT_TOOLS,
        "delete_file",
        # Session tools
        *COMMON_READ_TOOLS,
        *COMMON_WRITE_TOOLS,
    },

    WorkflowState.ANALYSIS: {
        # Agent tools (AnalysisAgent)
        "save_analysis_report",
        "calculate_pe_ratio",
        "calculate_debt_to_equity",
        "calculate_roe",
        # Skills (registry)
        "execute_python",
        "analyze_code",
        "generate_chart",
        "analyze_risk_metrics",
        # Memory skills
        "store_decision",
        "search_past_decisions",
        "search_entities",
        # Storage
        *ARTIFACT_TOOLS,
        "delete_file",
        # Session tools
        *COMMON_READ_TOOLS,
        *COMMON_WRITE_TOOLS,
    },

    WorkflowState.COMPLIANCE_CHECK: {
        # Agent tools (ComplianceAgent)
        "check_insider_trading_risk",
        "save_compliance_report",
        # Skills (registry)
        "kyc_verification",
        "aml_screening",
        "sanctions_check",
        "search_knowledge",
        # Memory skills
        "store_decision",
        "search_past_decisions",
        "search_entities",
        # Storage
        *ARTIFACT_TOOLS,
        # Session tools
        *COMMON_READ_TOOLS,
        *COMMON_WRITE_TOOLS,
    },

    WorkflowState.HUMAN_REVIEW: {
        # Read-only access during review
        *COMMON_READ_TOOLS,
        "read_artifact",
        "list_artifacts",
    },

    WorkflowState.REPORT_GENERATION: {
        # Agent tools (ResearchAgent, AnalysisAgent, ComplianceAgent)
        "save_research_report",
        "save_analysis_report",
        "save_compliance_report",
        # Skills (registry)
        "generate_chart",
        "search_knowledge",
        # Storage
        "read_artifact",
        "list_artifacts",
        "save_artifact",
        # Session tools
        *COMMON_READ_TOOLS,
        *COMMON_WRITE_TOOLS,
    },

    WorkflowState.COMPLETED: {
        # Read-only
        "read_artifact",
        "list_artifacts",
        "read_plan",
        "read_session_state",
    },

    WorkflowState.FAILED: {
        # Read-only diagnostics
        "read_plan",
        "read_session_state",
        *COMMON_READ_TOOLS,
    },
}

# Add planning skills to all states (Deep Work agents bypass state machine entirely,
# but compliance agents with state machine ON can still use planning skills)
for _state in STATE_TOOLS:
    STATE_TOOLS[_state] |= PLANNING_SKILLS


# =============================================================================
# State Transitions
# =============================================================================

STATE_TRANSITIONS: Dict[WorkflowState, Set[WorkflowState]] = {
    WorkflowState.INITIALIZED: {
        WorkflowState.DATA_GATHERING,
        WorkflowState.FAILED,
    },

    WorkflowState.DATA_GATHERING: {
        WorkflowState.ANALYSIS,
        WorkflowState.COMPLIANCE_CHECK,  # Can skip analysis for quick checks
        WorkflowState.FAILED,
    },

    WorkflowState.ANALYSIS: {
        WorkflowState.COMPLIANCE_CHECK,
        WorkflowState.REPORT_GENERATION,  # Simple analysis might skip compliance
        WorkflowState.FAILED,
    },

    WorkflowState.COMPLIANCE_CHECK: {
        WorkflowState.HUMAN_REVIEW,  # High-risk items need review
        WorkflowState.REPORT_GENERATION,  # Low-risk can proceed
        WorkflowState.FAILED,
    },

    WorkflowState.HUMAN_REVIEW: {
        WorkflowState.REPORT_GENERATION,  # Approved
        WorkflowState.FAILED,  # Rejected
    },

    WorkflowState.REPORT_GENERATION: {
        WorkflowState.COMPLETED,
        WorkflowState.FAILED,
    },

    # Terminal states - no transitions out
    WorkflowState.COMPLETED: set(),
    WorkflowState.FAILED: set(),
}


# =============================================================================
# State Context
# =============================================================================

class StateContext(BaseModel):
    """
    Persistent state context for a workflow session.

    Stored in session filesystem at /sessions/{user_id}/{session_id}/state.json
    """
    current_state: WorkflowState = WorkflowState.INITIALIZED
    previous_state: Optional[WorkflowState] = None
    state_history: List[Dict[str, Any]] = Field(default_factory=list)
    workflow_id: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Metadata for audit
    transition_count: int = 0
    tool_calls_in_state: int = 0

    class Config:
        use_enum_values = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "current_state": self.current_state.value if isinstance(self.current_state, WorkflowState) else self.current_state,
            "previous_state": self.previous_state.value if self.previous_state and isinstance(self.previous_state, WorkflowState) else self.previous_state,
            "state_history": self.state_history,
            "workflow_id": self.workflow_id,
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "transition_count": self.transition_count,
            "tool_calls_in_state": self.tool_calls_in_state,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StateContext":
        """Create from dictionary."""
        if "current_state" in data:
            data["current_state"] = WorkflowState(data["current_state"])
        if "previous_state" in data and data["previous_state"]:
            data["previous_state"] = WorkflowState(data["previous_state"])
        if "started_at" in data and isinstance(data["started_at"], str):
            data["started_at"] = datetime.fromisoformat(data["started_at"])
        if "updated_at" in data and isinstance(data["updated_at"], str):
            data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return cls(**data)


# =============================================================================
# State Machine Manager
# =============================================================================

class StateMachineManager:
    """
    Manages workflow state machine operations.

    Provides:
    - State validation
    - Tool access control
    - State persistence
    - Transition logging
    """

    def __init__(
        self,
        state_tools: Optional[Dict[WorkflowState, Set[str]]] = None,
        state_transitions: Optional[Dict[WorkflowState, Set[WorkflowState]]] = None,
    ):
        self.state_tools = state_tools or STATE_TOOLS
        self.state_transitions = state_transitions or STATE_TRANSITIONS

    def get_valid_tools(self, state: WorkflowState) -> Set[str]:
        """Get the set of valid tools for a given state."""
        return self.state_tools.get(state, set())

    def is_tool_valid(self, tool_name: str, state: WorkflowState) -> bool:
        """Check if a tool is valid in the given state."""
        valid_tools = self.get_valid_tools(state)
        return tool_name in valid_tools

    def validate_tool_access(
        self,
        tool_name: str,
        state: WorkflowState,
        raise_on_invalid: bool = True,
    ) -> bool:
        """
        Validate that a tool can be called in the current state.

        Args:
            tool_name: Name of the tool being called
            state: Current workflow state
            raise_on_invalid: If True, raise InvalidToolError for invalid access

        Returns:
            True if valid, False otherwise (if raise_on_invalid=False)

        Raises:
            InvalidToolError: If tool is not valid and raise_on_invalid=True
        """
        valid_tools = self.get_valid_tools(state)

        if tool_name not in valid_tools:
            if raise_on_invalid:
                raise InvalidToolError(tool_name, state, valid_tools)
            return False

        return True

    def get_valid_transitions(self, state: WorkflowState) -> Set[WorkflowState]:
        """Get valid next states from the current state."""
        return self.state_transitions.get(state, set())

    def validate_transition(
        self,
        current_state: WorkflowState,
        next_state: WorkflowState,
        raise_on_invalid: bool = True,
    ) -> bool:
        """
        Validate that a state transition is allowed.

        Args:
            current_state: Current workflow state
            next_state: Requested next state
            raise_on_invalid: If True, raise StateTransitionError for invalid transitions

        Returns:
            True if valid, False otherwise (if raise_on_invalid=False)

        Raises:
            StateTransitionError: If transition is invalid and raise_on_invalid=True
        """
        valid_transitions = self.get_valid_transitions(current_state)

        if next_state not in valid_transitions:
            if raise_on_invalid:
                raise StateTransitionError(
                    current_state, next_state, valid_transitions
                )
            return False

        return True

    def transition(
        self,
        context: StateContext,
        next_state: WorkflowState,
        reason: Optional[str] = None,
    ) -> StateContext:
        """
        Perform a state transition.

        Args:
            context: Current state context
            next_state: State to transition to
            reason: Optional reason for the transition

        Returns:
            Updated state context

        Raises:
            StateTransitionError: If transition is not valid
        """
        current_state = WorkflowState(context.current_state) if isinstance(context.current_state, str) else context.current_state

        # Validate transition
        self.validate_transition(current_state, next_state)

        # Record in history
        context.state_history.append({
            "from": current_state.value,
            "to": next_state.value,
            "timestamp": datetime.utcnow().isoformat(),
            "reason": reason,
            "tool_calls_in_previous_state": context.tool_calls_in_state,
        })

        # Update context
        context.previous_state = current_state
        context.current_state = next_state
        context.transition_count += 1
        context.tool_calls_in_state = 0
        context.updated_at = datetime.utcnow()

        logger.info(
            f"State transition: {current_state.value} -> {next_state.value} "
            f"(reason: {reason})"
        )

        return context

    async def persist_state(
        self,
        context: StateContext,
        session_path: Path,
    ) -> None:
        """
        Persist state context to session filesystem.

        Args:
            context: State context to persist
            session_path: Path to session directory
        """
        import aiofiles

        state_file = session_path / "state.json"

        async with aiofiles.open(state_file, "w") as f:
            await f.write(json.dumps(context.to_dict(), indent=2))

        logger.debug(f"Persisted state to {state_file}")

    async def load_state(
        self,
        session_path: Path,
    ) -> StateContext:
        """
        Load state context from session filesystem.

        Args:
            session_path: Path to session directory

        Returns:
            State context (new if not found)
        """
        import aiofiles

        state_file = session_path / "state.json"

        if not state_file.exists():
            logger.debug(f"No state file found at {state_file}, creating new context")
            return StateContext()

        try:
            async with aiofiles.open(state_file, "r") as f:
                content = await f.read()
                data = json.loads(content)
                return StateContext.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load state from {state_file}: {e}")
            return StateContext()


# =============================================================================
# Singleton Instance
# =============================================================================

_state_machine_manager: Optional[StateMachineManager] = None


def get_state_machine_manager() -> StateMachineManager:
    """Get the singleton state machine manager instance."""
    global _state_machine_manager

    if _state_machine_manager is None:
        _state_machine_manager = StateMachineManager()

    return _state_machine_manager


# =============================================================================
# Utility Functions
# =============================================================================

def get_valid_tools_for_state(state: WorkflowState) -> Set[str]:
    """Convenience function to get valid tools for a state."""
    return get_state_machine_manager().get_valid_tools(state)


def is_tool_valid_for_state(tool_name: str, state: WorkflowState) -> bool:
    """Convenience function to check tool validity."""
    return get_state_machine_manager().is_tool_valid(tool_name, state)


def get_all_states() -> List[WorkflowState]:
    """Get all workflow states."""
    return list(WorkflowState)


def get_terminal_states() -> Set[WorkflowState]:
    """Get states that have no valid transitions (terminal states)."""
    return {WorkflowState.COMPLETED, WorkflowState.FAILED}


def is_terminal_state(state: WorkflowState) -> bool:
    """Check if a state is terminal."""
    return state in get_terminal_states()
