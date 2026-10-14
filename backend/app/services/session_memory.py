"""
Session Memory Service

Implements session storage for plan/todo recitation pattern.
Supports both in-memory (Phase 2A) and filesystem backends.

The recitation pattern (from Manus AI research) achieves 42% error reduction by:
1. Including high-level plan (plan.md) in every agent turn
2. Including current todo list (todo.md) in every agent turn
3. Keeping agents focused on original goals during long sessions

Migration Path:
- Phase 2A: InMemorySessionMemory (default)
- Phase 2: FilesystemSessionMemory when USE_SESSION_FILESYSTEM=True

Feature Flags:
- USE_SESSION_FILESYSTEM: When True, uses filesystem-based SessionStorage
- USE_PLAN_TODO_RECITATION: When True, enables recitation pattern in agents
"""

from typing import Dict, Optional, Set
from datetime import datetime
from pydantic import BaseModel
import json

from app.config import get_settings

# Known default plan.md templates (InMemorySessionMemory + SessionStorage)
_DEFAULT_PLAN_MARKERS: Set[str] = {
    "# Session Plan\n\n*Define your high-level goal here.*\n",
    "# Session Plan\n\n*No plan defined yet. Define your high-level goal here.*\n",
}

# Known default todo.md templates (InMemorySessionMemory + SessionStorage)
_DEFAULT_TODO_MARKERS: Set[str] = {
    "# Todo List\n\n- [ ] First task\n",
    "# Todo List\n\n*No tasks defined yet.*\n",
}


def _is_default_content(plan: str, todo: str) -> bool:
    """Return True if both plan and todo are still their initial default templates."""
    return plan.strip() in {m.strip() for m in _DEFAULT_PLAN_MARKERS} and \
           todo.strip() in {m.strip() for m in _DEFAULT_TODO_MARKERS}


class SessionState(BaseModel):
    """State for a single session."""
    session_id: str
    user_id: str
    created_at: datetime
    updated_at: datetime
    files: Dict[str, str] = {}  # filename -> content


class InMemorySessionMemory:
    """
    In-memory session storage for Phase 2A.

    Stores session files (plan.md, todo.md, state.json) in memory
    until Phase 2 implements filesystem-based sessions.

    Thread-safe via single-threaded async event loop.

    Usage:
        memory = InMemorySessionMemory.get_instance()
        await memory.write(session_id, "plan.md", "# My Plan...")
        content = await memory.read(session_id, "plan.md")
    """

    _instance: Optional['InMemorySessionMemory'] = None
    _sessions: Dict[str, SessionState] = {}

    @classmethod
    def get_instance(cls) -> 'InMemorySessionMemory':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset for testing."""
        cls._instance = None
        cls._sessions = {}

    def _get_or_create_session(self, session_id: str, user_id: str = "unknown") -> SessionState:
        """Get existing session or create new one."""
        if session_id not in self._sessions:
            now = datetime.utcnow()
            self._sessions[session_id] = SessionState(
                session_id=session_id,
                user_id=user_id,
                created_at=now,
                updated_at=now,
                files={
                    # Default templates
                    "plan.md": "# Session Plan\n\n*No plan defined yet. Define your high-level goal here.*\n",
                    "todo.md": "# Todo List\n\n*No tasks defined yet.*\n",
                    "state.json": json.dumps({"status": "active", "step": 0}),
                }
            )
        return self._sessions[session_id]

    async def read(
        self,
        tenant_id: str,
        session_id: str,
        file_name: str,
        user_id: str = "unknown"
    ) -> str:
        """Read a file from session memory. tenant_id accepted for interface parity."""
        session = self._get_or_create_session(session_id, user_id)
        return session.files.get(file_name, "")

    async def write(
        self,
        tenant_id: str,
        session_id: str,
        file_name: str,
        content: str,
        user_id: str = "unknown"
    ) -> None:
        """Write a file to session memory. tenant_id accepted for interface parity."""
        session = self._get_or_create_session(session_id, user_id)
        session.files[file_name] = content
        session.updated_at = datetime.utcnow()

    async def append(
        self,
        tenant_id: str,
        session_id: str,
        file_name: str,
        content: str,
        user_id: str = "unknown"
    ) -> None:
        """Append content to a file in session memory. tenant_id accepted for interface parity."""
        session = self._get_or_create_session(session_id, user_id)
        existing = session.files.get(file_name, "")
        session.files[file_name] = existing + content
        session.updated_at = datetime.utcnow()

    async def list_files(self, tenant_id: str, session_id: str) -> list[str]:
        """List all files in a session."""
        if session_id not in self._sessions:
            return []
        return list(self._sessions[session_id].files.keys())

    async def delete_file(self, tenant_id: str, session_id: str, file_name: str) -> bool:
        """Delete a file from session memory."""
        if session_id not in self._sessions:
            return False
        if file_name in self._sessions[session_id].files:
            del self._sessions[session_id].files[file_name]
            return True
        return False

    async def delete_session(self, tenant_id: str, session_id: str) -> bool:
        """Delete an entire session."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    async def get_session_info(self, tenant_id: str, session_id: str) -> Optional[dict]:
        """Get session metadata."""
        if session_id not in self._sessions:
            return None
        session = self._sessions[session_id]
        return {
            "session_id": session.session_id,
            "user_id": session.user_id,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "file_count": len(session.files),
            "files": list(session.files.keys()),
        }


# ============================================================================
# Session Memory Factory
# ============================================================================

def get_session_memory():
    """
    Get the appropriate session memory implementation based on feature flags.

    Returns:
        InMemorySessionMemory when USE_SESSION_FILESYSTEM is False (default)
        FilesystemSessionMemory when USE_SESSION_FILESYSTEM is True

    Usage:
        memory = get_session_memory()
        await memory.write(session_id, "plan.md", "# My Plan...")
        content = await memory.read(session_id, "plan.md")
    """
    settings = get_settings()

    if getattr(settings, "USE_SESSION_FILESYSTEM", False):
        from app.services.session_storage import FilesystemSessionMemory
        return FilesystemSessionMemory.get_instance()
    else:
        return InMemorySessionMemory.get_instance()


# ============================================================================
# Convenience functions for direct import
# ============================================================================

async def read_session_file(tenant_id: str, session_id: str, file_name: str, user_id: str = "unknown") -> str:
    """Read a file from session memory."""
    memory = get_session_memory()
    return await memory.read(tenant_id, session_id, file_name, user_id)


async def write_session_file(tenant_id: str, session_id: str, file_name: str, content: str, user_id: str = "unknown") -> None:
    """Write a file to session memory."""
    memory = get_session_memory()
    await memory.write(tenant_id, session_id, file_name, content, user_id)


async def append_session_file(tenant_id: str, session_id: str, file_name: str, content: str, user_id: str = "unknown") -> None:
    """Append content to a file in session memory."""
    memory = get_session_memory()
    await memory.append(tenant_id, session_id, file_name, content, user_id)


async def list_session_files(tenant_id: str, session_id: str) -> list[str]:
    """List all files in a session."""
    memory = get_session_memory()
    return await memory.list_files(tenant_id, session_id)


async def delete_session(tenant_id: str, session_id: str) -> bool:
    """Delete an entire session."""
    memory = get_session_memory()
    return await memory.delete_session(tenant_id, session_id)


async def get_session_info(tenant_id: str, session_id: str) -> Optional[dict]:
    """Get session metadata."""
    memory = get_session_memory()
    return await memory.get_session_info(tenant_id, session_id)


async def get_recitation_context(tenant_id: str, session_id: str, user_id: str = "unknown") -> str:
    """
    Get the recitation context for an agent turn.

    Args:
        tenant_id: Tenant ID for isolation
        session_id: Session identifier
        user_id: User ID

    Returns:
        Formatted recitation context string
    """
    memory = get_session_memory()

    plan = await memory.read(tenant_id, session_id, "plan.md", user_id)
    todo = await memory.read(tenant_id, session_id, "todo.md", user_id)

    # Skip recitation when both files are still their default templates
    if _is_default_content(plan or "", todo or ""):
        return ""

    return f"""
## Current Plan
{plan}

## Current Todo List
{todo}

## Instructions
- Review the plan and todo list above before responding
- Update todo.md after completing tasks
- Save large outputs to artifacts/ directory
- Use the available workspace file tools and the sandboxed execute_python skill for file operations and data processing
"""
