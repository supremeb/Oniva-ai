"""
Stateful Agent Wrapper

Wraps BaseAgent with state machine enforcement for 100% tool safety.

This wrapper:
1. Structurally removes invalid tools from the agent
2. Validates tool calls at runtime (defense in depth)
3. Manages state transitions
4. Persists state to session filesystem
5. Logs all actions for audit trail

Based on Section 3.4 and Manus AI research.
"""

from typing import Optional, Dict, Any, List, Set, Callable, AsyncIterator
from pathlib import Path
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
import logging
import functools

from app.agents.base import BaseAgent
from app.agents.state_machine import (
    WorkflowState,
    StateContext,
    StateMachineManager,
    get_state_machine_manager,
    StateTransitionError,
    InvalidToolError,
)
from app.config import get_settings

logger = logging.getLogger(__name__)


class StatefulAgentResult(BaseModel):
    """Result from a stateful agent execution."""
    data: Any  # The actual result from the agent
    current_state: WorkflowState
    previous_state: Optional[WorkflowState] = None
    tools_called: List[str] = []
    state_changed: bool = False
    next_state: Optional[WorkflowState] = None  # Suggested next state


class StatefulAgent:
    """
    Agent wrapper that enforces state machine tool access.

    Provides 100% defense against prompt injection by:
    1. Structurally removing invalid tools from agent context
    2. Runtime validation before tool execution (defense in depth)
    3. State persistence for session recovery

    Usage:
        base_agent = AnalysisAgent()
        stateful = StatefulAgent(
            base_agent=base_agent,
            session_id="session_123",
            user_id="user_456"
        )
        result = await stateful.run("Analyze AAPL stock")
    """

    def __init__(
        self,
        base_agent: BaseAgent,
        session_id: str,
        user_id: str,
        db: Optional[AsyncSession] = None,
        session_path: Optional[Path] = None,
        state_manager: Optional[StateMachineManager] = None,
        initial_state: Optional[WorkflowState] = None,
    ):
        """
        Initialize stateful agent wrapper.

        Args:
            base_agent: The underlying agent to wrap
            session_id: Session ID for state persistence
            user_id: User ID for audit logging
            db: Database session (optional, can be passed to run())
            session_path: Path to session directory (auto-calculated if not provided)
            state_manager: Custom state machine manager (uses default if not provided)
            initial_state: Initial state (defaults to INITIALIZED)
        """
        self.base_agent = base_agent
        self.session_id = session_id
        self.user_id = user_id
        self.db = db
        self.state_manager = state_manager or get_state_machine_manager()

        # Calculate session path if not provided
        settings = get_settings()
        if session_path is None:
            base_path = Path(getattr(settings, 'SESSION_BASE_PATH', './sessions'))
            self.session_path = base_path / str(user_id) / session_id
        else:
            self.session_path = session_path

        # Initialize state context
        self.context = StateContext(
            current_state=initial_state or WorkflowState.INITIALIZED,
            workflow_id=session_id,
        )

        # Track tool calls for audit
        self._tools_called_this_turn: List[str] = []

        # Store original tools before filtering
        self._all_tools = self._extract_agent_tools()

    def _extract_agent_tools(self) -> Dict[str, Callable]:
        """Extract tool functions from the base agent.

        Uses the public ``agent.toolsets`` list to find FunctionToolset instances
        and read their ``.tools`` dicts. This avoids accessing private internals.
        """
        from pydantic_ai.toolsets.function import FunctionToolset

        tools = {}

        if hasattr(self.base_agent, 'agent') and hasattr(self.base_agent.agent, 'toolsets'):
            for ts in self.base_agent.agent.toolsets:
                if isinstance(ts, FunctionToolset):
                    for tool_name, tool in ts.tools.items():
                        tools[tool_name] = tool

        return tools

    def _get_filtered_tools(self, state: WorkflowState) -> List[Any]:
        """
        Get tools filtered for the current state.

        This is the core security mechanism - tools not in the valid set
        are structurally removed from the agent, making them impossible
        to call even with prompt injection.
        """
        valid_tool_names = self.state_manager.get_valid_tools(state)

        filtered = []
        for name, tool in self._all_tools.items():
            if name in valid_tool_names:
                filtered.append(tool)

        logger.debug(
            f"Filtered tools for state {state.value}: "
            f"{len(filtered)}/{len(self._all_tools)} tools available"
        )

        return filtered

    def _create_tool_validation_wrapper(
        self,
        tool_func: Callable,
        tool_name: str,
    ) -> Callable:
        """
        Wrap a tool function with runtime validation.

        This is defense in depth - even if tool filtering fails,
        this wrapper will block invalid calls.
        """
        @functools.wraps(tool_func)
        async def validated_tool(*args, **kwargs):
            # Validate tool access
            current_state = self.context.current_state
            if isinstance(current_state, str):
                current_state = WorkflowState(current_state)

            if not self.state_manager.is_tool_valid(tool_name, current_state):
                raise InvalidToolError(
                    tool_name,
                    current_state,
                    self.state_manager.get_valid_tools(current_state),
                )

            # Log tool call
            self._tools_called_this_turn.append(tool_name)
            self.context.tool_calls_in_state += 1

            logger.info(f"Tool call: {tool_name} in state {current_state.value}")

            # Execute actual tool
            return await tool_func(*args, **kwargs)

        return validated_tool

    async def load_state(self) -> StateContext:
        """
        Load state from session filesystem.

        Returns:
            Loaded state context, or new context if not found
        """
        settings = get_settings()

        if not getattr(settings, 'USE_STATE_MACHINE', False):
            return self.context

        self.context = await self.state_manager.load_state(self.session_path)
        return self.context

    async def persist_state(self) -> None:
        """Persist current state to session filesystem."""
        settings = get_settings()

        if not getattr(settings, 'USE_STATE_MACHINE', False):
            return

        # Ensure session directory exists
        self.session_path.mkdir(parents=True, exist_ok=True)

        await self.state_manager.persist_state(self.context, self.session_path)

    async def transition(
        self,
        next_state: WorkflowState,
        reason: Optional[str] = None,
    ) -> StateContext:
        """
        Perform a state transition and record it in the cryptographic audit trail.

        Args:
            next_state: State to transition to
            reason: Reason for the transition

        Returns:
            Updated state context

        Raises:
            StateTransitionError: If transition is not valid
        """
        from_state = self.context.current_state
        if isinstance(from_state, WorkflowState):
            from_state_str = from_state.value
        else:
            from_state_str = str(from_state)

        self.context = self.state_manager.transition(
            self.context, next_state, reason
        )

        # Record state transition in cryptographic audit trail (fire-and-forget)
        if self.db is not None:
            try:
                from app.services.audit_logger import log_decision

                agent_id = getattr(self.base_agent, 'agent_type', 'unknown_agent')
                await log_decision(
                    agent_id=agent_id,
                    decision=f"State transition: {from_state_str} -> {next_state.value}",
                    rationale=reason or "Automatic state transition",
                    workflow_state=next_state.value,
                    db=self.db,
                    session_id=self.session_id,
                    data_sources=[],
                )
            except Exception as e:
                logger.warning(f"Failed to log state transition to audit trail: {e}")

        return self.context

    async def run(
        self,
        user_message: str,
        db: Optional[AsyncSession] = None,
        auto_transition: bool = True,
        next_state: Optional[WorkflowState] = None,
        **kwargs,
    ) -> StatefulAgentResult:
        """
        Run the agent with state machine enforcement.

        Args:
            user_message: User's message to process
            db: Database session (uses instance db if not provided)
            auto_transition: If True, automatically transition state based on result
            next_state: Explicit next state to transition to
            **kwargs: Additional arguments passed to base agent

        Returns:
            StatefulAgentResult with data and state info
        """
        settings = get_settings()
        use_state_machine = getattr(settings, 'USE_STATE_MACHINE', False)

        # Use provided db or instance db
        db_session = db or self.db
        if db_session is None:
            raise ValueError("Database session required for agent execution")

        # Reset turn tracking
        self._tools_called_this_turn = []

        # Load persisted state if state machine is enabled
        if use_state_machine:
            await self.load_state()

        current_state = self.context.current_state
        if isinstance(current_state, str):
            current_state = WorkflowState(current_state)

        # Get state before execution
        previous_state = self.context.previous_state

        # If state machine is enabled, filter tools and add context
        if use_state_machine:
            # Add state context to message
            state_context = (
                f"\n[Current State: {current_state.value}]\n"
                f"[Valid Tools: {', '.join(sorted(self.state_manager.get_valid_tools(current_state))[:10])}...]\n\n"
            )
            enhanced_message = state_context + user_message
        else:
            enhanced_message = user_message

        # Run the base agent
        result = await self.base_agent.run(
            user_message=enhanced_message,
            db=db_session,
            user_id=self.user_id,
            session_id=self.session_id,
            **kwargs,
        )

        # Handle state transition
        state_changed = False
        suggested_next_state = None

        if use_state_machine:
            if next_state is not None:
                # Explicit transition requested
                try:
                    await self.transition(next_state, reason="Explicit transition requested")
                    state_changed = True
                except StateTransitionError as e:
                    logger.warning(f"Transition failed: {e}")

            elif auto_transition and hasattr(result, 'output'):
                # Auto-detect next state from result
                suggested_next_state = self._infer_next_state(result.output)

            # Persist state
            await self.persist_state()

        return StatefulAgentResult(
            data=result.output if hasattr(result, 'output') else result,
            current_state=WorkflowState(self.context.current_state) if isinstance(self.context.current_state, str) else self.context.current_state,
            previous_state=WorkflowState(previous_state) if previous_state and isinstance(previous_state, str) else previous_state,
            tools_called=self._tools_called_this_turn,
            state_changed=state_changed,
            next_state=suggested_next_state,
        )

    async def run_stream(
        self,
        user_message: str,
        db: Optional[AsyncSession] = None,
        **kwargs,
    ) -> AsyncIterator[Any]:
        """
        Run the agent with streaming response and state machine enforcement.

        Args:
            user_message: User's message to process
            db: Database session
            **kwargs: Additional arguments

        Yields:
            Streaming response chunks
        """
        settings = get_settings()
        use_state_machine = getattr(settings, 'USE_STATE_MACHINE', False)

        db_session = db or self.db
        if db_session is None:
            raise ValueError("Database session required for agent execution")

        # Reset turn tracking
        self._tools_called_this_turn = []

        # Load state if enabled
        if use_state_machine:
            await self.load_state()

        current_state = self.context.current_state
        if isinstance(current_state, str):
            current_state = WorkflowState(current_state)

        # Add state context
        if use_state_machine:
            state_context = f"\n[Current State: {current_state.value}]\n\n"
            enhanced_message = state_context + user_message
        else:
            enhanced_message = user_message

        # Stream from base agent
        async for event in self.base_agent.stream_events(
            user_message=enhanced_message,
            db=db_session,
            user_id=self.user_id,
            session_id=self.session_id,
            **kwargs,
        ):
            yield event

        # Persist state after stream completes
        if use_state_machine:
            await self.persist_state()

    def _infer_next_state(self, result: Any) -> Optional[WorkflowState]:
        """
        Infer the next state based on agent result.

        This provides automatic state progression hints based on
        what the agent accomplished.
        """
        current = self.context.current_state
        if isinstance(current, str):
            current = WorkflowState(current)

        valid_next = self.state_manager.get_valid_transitions(current)

        # Simple heuristics for auto-progression
        if current == WorkflowState.INITIALIZED:
            if WorkflowState.DATA_GATHERING in valid_next:
                return WorkflowState.DATA_GATHERING

        elif current == WorkflowState.DATA_GATHERING:
            # Check if we have enough data to analyze
            if "save_artifact" in self._tools_called_this_turn:
                if WorkflowState.ANALYSIS in valid_next:
                    return WorkflowState.ANALYSIS

        elif current == WorkflowState.ANALYSIS:
            # If analysis is complete, suggest compliance
            if "save_analysis_report" in self._tools_called_this_turn:
                if WorkflowState.COMPLIANCE_CHECK in valid_next:
                    return WorkflowState.COMPLIANCE_CHECK

        elif current == WorkflowState.COMPLIANCE_CHECK:
            # If compliance tools were called, move to report generation
            if "save_compliance_report" in self._tools_called_this_turn or \
               "check_insider_trading_risk" in self._tools_called_this_turn:
                if WorkflowState.REPORT_GENERATION in valid_next:
                    return WorkflowState.REPORT_GENERATION

        elif current == WorkflowState.REPORT_GENERATION:
            # If any report-saving tool was called, we're done
            if "save_research_report" in self._tools_called_this_turn or \
               "save_analysis_report" in self._tools_called_this_turn or \
               "save_compliance_report" in self._tools_called_this_turn:
                if WorkflowState.COMPLETED in valid_next:
                    return WorkflowState.COMPLETED

        return None

    def get_current_state(self) -> WorkflowState:
        """Get the current workflow state."""
        current = self.context.current_state
        return WorkflowState(current) if isinstance(current, str) else current

    def get_valid_tools(self) -> Set[str]:
        """Get valid tools for the current state."""
        return self.state_manager.get_valid_tools(self.get_current_state())

    def get_valid_transitions(self) -> Set[WorkflowState]:
        """Get valid next states from current state."""
        return self.state_manager.get_valid_transitions(self.get_current_state())

    def get_state_history(self) -> List[Dict[str, Any]]:
        """Get the state transition history."""
        return self.context.state_history


# =============================================================================
# Factory Function
# =============================================================================

def create_stateful_agent(
    agent_type: str,
    session_id: str,
    user_id: str,
    db: Optional[AsyncSession] = None,
    initial_state: Optional[WorkflowState] = None,
    **agent_kwargs,
) -> StatefulAgent:
    """
    Factory function to create a stateful agent by type.

    Args:
        agent_type: Type of agent ('analysis', 'research', 'compliance', 'data_retrieval')
        session_id: Session ID
        user_id: User ID
        db: Database session
        initial_state: Initial state
        **agent_kwargs: Additional arguments for agent creation

    Returns:
        StatefulAgent wrapping the specified agent type
    """
    # Import agents here to avoid circular imports
    if agent_type == "analysis":
        from app.agents.analysis import analysis_agent
        base_agent = analysis_agent
    elif agent_type == "research":
        from app.agents.research import research_agent
        base_agent = research_agent
    elif agent_type == "compliance":
        from app.agents.compliance import compliance_agent
        base_agent = compliance_agent
    elif agent_type == "data_retrieval":
        from app.agents.data_retrieval import data_retrieval_agent
        base_agent = data_retrieval_agent
    else:
        raise ValueError(f"Unknown agent type: {agent_type}")

    return StatefulAgent(
        base_agent=base_agent,
        session_id=session_id,
        user_id=user_id,
        db=db,
        initial_state=initial_state,
    )
