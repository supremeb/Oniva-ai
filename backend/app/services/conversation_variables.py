"""
Conversation Variables Service
Vellum-inspired persistent variables across workflow conversation turns

Supports:
- Variable declaration in workflow spec
- Type-safe variable storage and retrieval
- Conversation-scoped persistence
- Variable history tracking
- Expression evaluation for computed variables

This allows workflows to maintain state across multiple conversation turns,
enabling features like:
- Accumulating context from multiple messages
- Tracking conversation state
- Building up data structures over time
- Implementing stateful chatbot behaviors
"""

import json
import logging
from typing import Dict, Any, List, Optional, Union, Set, TYPE_CHECKING
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.services.session_storage import SessionStorage

logger = logging.getLogger(__name__)


class VariableType(str, Enum):
    """Supported variable types"""
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"
    ANY = "any"


class VariableDefinition(BaseModel):
    """Definition of a conversation variable"""
    name: str
    type: VariableType = VariableType.STRING
    default: Optional[Any] = None
    description: Optional[str] = None
    required: bool = False
    persist: bool = True  # Whether to persist across turns
    max_history: int = 10  # Max history entries to keep


class VariableValue(BaseModel):
    """A variable value with metadata"""
    value: Any
    type: VariableType
    updated_at: str
    turn_number: int
    source_node: Optional[str] = None


class VariableHistory(BaseModel):
    """History of a variable's values"""
    name: str
    current: Optional[VariableValue] = None
    history: List[VariableValue] = Field(default_factory=list)


class ConversationState(BaseModel):
    """Complete state of a conversation's variables"""
    session_id: str
    workflow_id: str
    turn_number: int = 0
    variables: Dict[str, VariableHistory] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def __init__(self, **data):
        if not data.get("created_at"):
            data["created_at"] = datetime.utcnow().isoformat()
        if not data.get("updated_at"):
            data["updated_at"] = datetime.utcnow().isoformat()
        super().__init__(**data)


class ConversationVariablesService:
    """
    Service for managing conversation variables

    Variables are scoped to a conversation session and can persist
    across multiple workflow execution turns. This enables stateful
    conversations where the workflow can remember and build upon
    previous interactions.

    Usage:
    1. Define variables in workflow spec:
       ```
       spec.conversation_variables = [
           {"name": "context", "type": "array", "default": []},
           {"name": "user_name", "type": "string"},
           {"name": "message_count", "type": "number", "default": 0}
       ]
       ```

    2. Access in nodes via {{conversation.variable_name}}

    3. Update via VariableAssigner node or programmatically
    """

    def __init__(self):
        # In-memory store for development
        # In production, this would use Redis or database
        self._sessions: Dict[str, ConversationState] = {}
        # Write coalescing: track modified sessions to flush at turn end
        self._dirty_sessions: Set[str] = set()
        # Optional storage backend for persistence
        self._storage: Optional["SessionStorage"] = None

    def get_or_create_session(
        self,
        session_id: str,
        workflow_id: str,
        variable_definitions: Optional[List[VariableDefinition]] = None
    ) -> ConversationState:
        """
        Get existing session or create new one with initialized variables

        Args:
            session_id: Unique session identifier
            workflow_id: Workflow this session belongs to
            variable_definitions: Variable definitions from workflow spec

        Returns:
            ConversationState with initialized variables
        """
        key = f"{workflow_id}:{session_id}"

        if key not in self._sessions:
            state = ConversationState(
                session_id=session_id,
                workflow_id=workflow_id,
                turn_number=0,
            )

            # Initialize variables from definitions
            if variable_definitions:
                for var_def in variable_definitions:
                    state.variables[var_def.name] = VariableHistory(
                        name=var_def.name,
                        current=VariableValue(
                            value=var_def.default,
                            type=var_def.type,
                            updated_at=datetime.utcnow().isoformat(),
                            turn_number=0
                        ) if var_def.default is not None else None,
                        history=[]
                    )

            self._sessions[key] = state

        return self._sessions[key]

    def get_session(
        self,
        session_id: str,
        workflow_id: str
    ) -> Optional[ConversationState]:
        """Get existing session without creating"""
        key = f"{workflow_id}:{session_id}"
        return self._sessions.get(key)

    def start_turn(
        self,
        session_id: str,
        workflow_id: str
    ) -> int:
        """
        Start a new conversation turn

        Returns the new turn number
        """
        state = self.get_or_create_session(session_id, workflow_id)
        state.turn_number += 1
        state.updated_at = datetime.utcnow().isoformat()
        return state.turn_number

    def set_variable(
        self,
        session_id: str,
        workflow_id: str,
        name: str,
        value: Any,
        source_node: Optional[str] = None,
        var_type: Optional[VariableType] = None
    ) -> VariableValue:
        """
        Set a variable value

        Args:
            session_id: Session identifier
            workflow_id: Workflow identifier
            name: Variable name
            value: Value to set
            source_node: Node that set this value
            var_type: Override variable type

        Returns:
            The created VariableValue
        """
        state = self.get_or_create_session(session_id, workflow_id)

        # Infer type if not provided
        if var_type is None:
            var_type = self._infer_type(value)

        # Create new value
        new_value = VariableValue(
            value=value,
            type=var_type,
            updated_at=datetime.utcnow().isoformat(),
            turn_number=state.turn_number,
            source_node=source_node
        )

        # Update history
        if name not in state.variables:
            state.variables[name] = VariableHistory(name=name)

        var_history = state.variables[name]

        # Move current to history
        if var_history.current is not None:
            var_history.history.append(var_history.current)
            # Trim history to max length
            if len(var_history.history) > 10:  # Default max
                var_history.history = var_history.history[-10:]

        var_history.current = new_value
        state.updated_at = datetime.utcnow().isoformat()

        return new_value

    def get_variable(
        self,
        session_id: str,
        workflow_id: str,
        name: str,
        default: Any = None
    ) -> Any:
        """
        Get a variable's current value

        Args:
            session_id: Session identifier
            workflow_id: Workflow identifier
            name: Variable name
            default: Default value if not set

        Returns:
            Current variable value or default
        """
        state = self.get_session(session_id, workflow_id)
        if not state:
            return default

        var_history = state.variables.get(name)
        if not var_history or not var_history.current:
            return default

        return var_history.current.value

    def get_variable_history(
        self,
        session_id: str,
        workflow_id: str,
        name: str
    ) -> Optional[VariableHistory]:
        """Get full history of a variable"""
        state = self.get_session(session_id, workflow_id)
        if not state:
            return None
        return state.variables.get(name)

    def get_all_variables(
        self,
        session_id: str,
        workflow_id: str
    ) -> Dict[str, Any]:
        """
        Get all current variable values as a flat dictionary

        Useful for passing to template substitution
        """
        state = self.get_session(session_id, workflow_id)
        if not state:
            return {}

        result = {}
        for name, var_history in state.variables.items():
            if var_history.current is not None:
                result[name] = var_history.current.value

        return result

    def append_to_array(
        self,
        session_id: str,
        workflow_id: str,
        name: str,
        value: Any,
        source_node: Optional[str] = None,
        max_length: Optional[int] = None
    ) -> List[Any]:
        """
        Append value to an array variable

        Args:
            session_id: Session identifier
            workflow_id: Workflow identifier
            name: Variable name
            value: Value to append
            source_node: Node that appended this value
            max_length: Optional max array length (removes oldest)

        Returns:
            Updated array
        """
        current = self.get_variable(session_id, workflow_id, name, [])
        if not isinstance(current, list):
            current = [current] if current is not None else []

        current.append(value)

        # Trim if max_length specified
        if max_length and len(current) > max_length:
            current = current[-max_length:]

        self.set_variable(
            session_id, workflow_id, name, current,
            source_node=source_node,
            var_type=VariableType.ARRAY
        )
        return current

    def update_object(
        self,
        session_id: str,
        workflow_id: str,
        name: str,
        updates: Dict[str, Any],
        source_node: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update fields in an object variable

        Args:
            session_id: Session identifier
            workflow_id: Workflow identifier
            name: Variable name
            updates: Dictionary of updates to merge
            source_node: Node that updated this

        Returns:
            Updated object
        """
        current = self.get_variable(session_id, workflow_id, name, {})
        if not isinstance(current, dict):
            current = {}

        current.update(updates)

        self.set_variable(
            session_id, workflow_id, name, current,
            source_node=source_node,
            var_type=VariableType.OBJECT
        )
        return current

    def increment(
        self,
        session_id: str,
        workflow_id: str,
        name: str,
        delta: Union[int, float] = 1,
        source_node: Optional[str] = None
    ) -> Union[int, float]:
        """
        Increment a numeric variable

        Args:
            session_id: Session identifier
            workflow_id: Workflow identifier
            name: Variable name
            delta: Amount to increment by
            source_node: Node that incremented

        Returns:
            New value
        """
        current = self.get_variable(session_id, workflow_id, name, 0)
        if not isinstance(current, (int, float)):
            current = 0

        new_value = current + delta

        self.set_variable(
            session_id, workflow_id, name, new_value,
            source_node=source_node,
            var_type=VariableType.NUMBER
        )
        return new_value

    def clear_session(
        self,
        session_id: str,
        workflow_id: str
    ) -> bool:
        """Clear all variables for a session"""
        key = f"{workflow_id}:{session_id}"
        if key in self._sessions:
            del self._sessions[key]
            return True
        return False

    def reset_variable(
        self,
        session_id: str,
        workflow_id: str,
        name: str,
        default: Any = None
    ) -> None:
        """Reset a variable to its default value"""
        state = self.get_session(session_id, workflow_id)
        if not state:
            return

        if name in state.variables:
            var_history = state.variables[name]
            # Move current to history before clearing
            if var_history.current:
                var_history.history.append(var_history.current)
            var_history.current = VariableValue(
                value=default,
                type=self._infer_type(default),
                updated_at=datetime.utcnow().isoformat(),
                turn_number=state.turn_number,
                source_node="system_reset"
            ) if default is not None else None

    def get_conversation_context(
        self,
        session_id: str,
        workflow_id: str,
        include_history: bool = False
    ) -> Dict[str, Any]:
        """
        Get conversation context for template substitution

        Returns a dictionary that can be used for {{conversation.X}} substitution
        """
        state = self.get_session(session_id, workflow_id)
        if not state:
            return {
                "turn_number": 0,
                "variables": {}
            }

        context = {
            "session_id": session_id,
            "workflow_id": workflow_id,
            "turn_number": state.turn_number,
            "variables": self.get_all_variables(session_id, workflow_id),
        }

        if include_history:
            context["history"] = {
                name: [v.model_dump() for v in var.history]
                for name, var in state.variables.items()
            }

        return context

    def export_session(
        self,
        session_id: str,
        workflow_id: str
    ) -> Optional[Dict[str, Any]]:
        """Export session state for persistence or debugging"""
        state = self.get_session(session_id, workflow_id)
        if not state:
            return None
        return state.model_dump()

    def import_session(
        self,
        session_data: Dict[str, Any]
    ) -> ConversationState:
        """Import session state from exported data"""
        state = ConversationState(**session_data)
        key = f"{state.workflow_id}:{state.session_id}"
        self._sessions[key] = state
        return state

    # =========================================================================
    # Phase 3: Persistence with Write Coalescing
    # =========================================================================

    def set_storage(self, storage: "SessionStorage") -> None:
        """
        Set the storage backend for persistence.

        Called during app startup to enable filesystem persistence.
        Without storage set, variables are in-memory only.

        Args:
            storage: SessionStorage instance for filesystem operations
        """
        self._storage = storage

    def mark_dirty(self, tenant_id: str, session_id: str, workflow_id: str) -> None:
        """
        Mark a session as needing persistence.

        Call this after set_variable() to track which sessions
        need to be flushed at turn end (write coalescing).

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation
            session_id: Session identifier
            workflow_id: Workflow identifier
        """
        key = f"{tenant_id}:{workflow_id}:{session_id}"
        self._dirty_sessions.add(key)

    async def flush_session(self, tenant_id: str, session_id: str, workflow_id: str) -> bool:
        """
        Flush dirty variables to filesystem.

        Call at END OF TURN for write coalescing (not per-variable).
        This reduces I/O from N writes per turn to 1 write per turn.

        Variables are stored in state.json under a workflow namespace:
        {"variables_by_workflow": {"workflow_123": {...}}}

        This prevents collision when multiple workflows share a session.

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation
            session_id: Session identifier
            workflow_id: Workflow identifier

        Returns:
            True if flushed, False if nothing to flush or no storage
        """
        if not self._storage:
            return False

        key = f"{tenant_id}:{workflow_id}:{session_id}"
        if key not in self._dirty_sessions:
            return False

        # Export session state
        state_data = self.export_session(session_id, workflow_id)
        if not state_data:
            self._dirty_sessions.discard(key)
            return False

        try:
            # Read current state.json
            state_content = await self._storage.read(tenant_id, session_id, "state.json")
            state = json.loads(state_content) if state_content else {}

            # Initialize variables_by_workflow if needed
            if "variables_by_workflow" not in state:
                state["variables_by_workflow"] = {}

            # Store under workflow namespace
            state["variables_by_workflow"][workflow_id] = state_data
            state["updated_at"] = datetime.utcnow().isoformat()

            # Write back
            await self._storage.write(
                tenant_id,
                session_id,
                "state.json",
                json.dumps(state, indent=2)
            )

            self._dirty_sessions.discard(key)
            logger.debug(
                f"Flushed variables for workflow {workflow_id} "
                f"in session {session_id}"
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to flush variables for {workflow_id}:{session_id}: {e}"
            )
            return False

    async def load_persisted_session(
        self,
        tenant_id: str,
        session_id: str,
        workflow_id: str,
    ) -> Optional[ConversationState]:
        """
        Load persisted variables from filesystem on session start.

        Looks for workflow-namespaced variables in state.json:
        {"variables_by_workflow": {"workflow_123": {...}}}

        Args:
            tenant_id: Tenant identifier for multi-tenant isolation
            session_id: Session identifier
            workflow_id: Workflow identifier

        Returns:
            ConversationState if found and loaded, None otherwise
        """
        if not self._storage:
            return None

        try:
            state_content = await self._storage.read(tenant_id, session_id, "state.json")
            if not state_content:
                return None

            state = json.loads(state_content)

            # Look for workflow-namespaced variables
            workflow_vars = state.get("variables_by_workflow", {}).get(workflow_id)
            if not workflow_vars:
                return None

            # Import the session state
            return self.import_session(workflow_vars)

        except Exception as e:
            logger.warning(
                f"Failed to load persisted session {workflow_id}:{session_id}: {e}"
            )
            return None

    async def flush_all(self) -> int:
        """
        Flush all dirty sessions.

        Call on application shutdown to persist any pending state.

        Returns:
            Number of sessions flushed
        """
        flushed = 0
        for key in list(self._dirty_sessions):
            parts = key.split(":", 2)
            if len(parts) == 3:
                tenant_id, workflow_id, session_id = parts
                if await self.flush_session(tenant_id, session_id, workflow_id):
                    flushed += 1
        return flushed

    def _infer_type(self, value: Any) -> VariableType:
        """Infer variable type from value"""
        if value is None:
            return VariableType.ANY
        if isinstance(value, bool):
            return VariableType.BOOLEAN
        if isinstance(value, (int, float)):
            return VariableType.NUMBER
        if isinstance(value, str):
            return VariableType.STRING
        if isinstance(value, list):
            return VariableType.ARRAY
        if isinstance(value, dict):
            return VariableType.OBJECT
        return VariableType.ANY


# Singleton instance
conversation_variables_service = ConversationVariablesService()


# --- Integration Helpers ---

def parse_variable_definitions(
    workflow_spec: Dict[str, Any]
) -> List[VariableDefinition]:
    """
    Parse variable definitions from workflow spec

    Looks in:
    - spec.conversation_variables
    - spec.variables (if marked as conversation-scoped)
    - Start node variables
    """
    definitions = []

    # From explicit conversation_variables
    conv_vars = workflow_spec.get("conversation_variables", [])
    for var in conv_vars:
        if isinstance(var, dict):
            definitions.append(VariableDefinition(
                name=var.get("name", ""),
                type=VariableType(var.get("type", "string")),
                default=var.get("default"),
                description=var.get("description"),
                required=var.get("required", False),
                persist=var.get("persist", True),
                max_history=var.get("max_history", 10)
            ))

    # From general variables marked as conversation-scoped
    general_vars = workflow_spec.get("variables", [])
    for var in general_vars:
        if isinstance(var, dict) and var.get("scope") == "conversation":
            definitions.append(VariableDefinition(
                name=var.get("name", ""),
                type=VariableType(var.get("type", "string")),
                default=var.get("default"),
                description=var.get("description"),
                required=var.get("required", False),
                persist=True,
                max_history=var.get("max_history", 10)
            ))

    return definitions


def initialize_conversation_for_workflow(
    session_id: str,
    workflow_id: str,
    workflow_spec: Dict[str, Any]
) -> ConversationState:
    """
    Initialize conversation variables for a workflow session

    This should be called when starting a new conversation session
    """
    definitions = parse_variable_definitions(workflow_spec)
    return conversation_variables_service.get_or_create_session(
        session_id=session_id,
        workflow_id=workflow_id,
        variable_definitions=definitions
    )
