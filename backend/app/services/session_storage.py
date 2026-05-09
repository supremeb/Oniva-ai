"""
Session Storage for Oniva Simplified Memory Architecture.

Sessions store the agent's working state with automatic filesystem persistence.
This enables 8+ hour sessions, crash recovery, and session teleportation.

Session Directory Structure (tenant-isolated):
    /sessions/{tenant_id}/{session_id}/
    |-- plan.md              # High-level strategy (recited every turn)
    |-- todo.md              # Current tasks (recited every turn)
    |-- state.json           # Machine-readable FSM state
    |-- logs/
    |   |-- history.md       # Narrative action log
    |   |-- errors.log       # Preserved failure traces
    |   |-- audit.jsonl      # Cryptographic audit trail (future)
    |-- volumes/             # Symlinks to mounted volumes (managed by VolumeManager)
    |-- artifacts/           # Symlinks or local artifacts (managed by ArtifactStorage)

Features:
- Simple filesystem-based storage (no promotion logic)
- DBOS checkpointing integration for crash recovery
- Recitation pattern support (plan.md + todo.md)
- Multi-tenant isolation: all paths scoped by tenant_id
- Backward compatible with InMemorySessionMemory API
"""

import json
import shutil
import asyncio
from pathlib import Path
from typing import Optional
from datetime import datetime
from dataclasses import dataclass

import aiofiles
import aiofiles.os

from app.config import get_settings


@dataclass
class SessionInfo:
    """Metadata about a session."""
    session_id: str
    user_id: str
    path: str
    created_at: datetime
    updated_at: datetime
    file_count: int = 0
    size_bytes: int = 0
    status: str = "active"


class SessionStorage:
    """
    Manages session working state with filesystem persistence.

    Simple filesystem-based storage with no promotion logic.
    Sessions are automatically checkpointed and can be restored.

    Usage:
        storage = SessionStorage()

        # Create a new session
        info = await storage.create_session("session_123", "user_456")

        # Read and write files
        await storage.write("session_123", "plan.md", "# My Plan...")
        content = await storage.read("session_123", "plan.md")

        # Get recitation context for agent prompts
        context = await storage.get_recitation_context("session_123")
    """

    def __init__(
        self,
        base_path: Optional[Path] = None,
        checkpointing_enabled: bool = True,
    ):
        """
        Initialize SessionStorage.

        Args:
            base_path: Base directory for sessions. Defaults to settings.SESSION_BASE_PATH
            checkpointing_enabled: If True, integrate with DBOS checkpointing
        """
        settings = get_settings()
        self.base_path = base_path or Path(settings.SESSION_BASE_PATH)
        self.base_path = Path(self.base_path)
        self.checkpointing_enabled = checkpointing_enabled

        # Default file templates
        self._default_files = {
            "plan.md": "# Session Plan\n\n*Define your high-level goal here.*\n",
            "todo.md": "# Todo List\n\n- [ ] First task\n",
            "state.json": json.dumps({
                "status": "active",
                "step": 0,
                "created_at": None,  # Will be set on creation
            }),
        }

    @staticmethod
    def _sanitize(value: str, label: str) -> str:
        """Sanitize a path component to prevent path traversal."""
        safe = "".join(c for c in value if c.isalnum() or c in "-_")
        if not safe or safe != value:
            raise ValueError(f"Invalid {label}: {value}")
        return safe

    def _get_session_path(
        self, tenant_id: str, session_id: str, agent_id: Optional[str] = None
    ) -> Path:
        """Get the filesystem path for a session, scoped by tenant and optionally by agent."""
        safe_tenant = self._sanitize(str(tenant_id), "tenant_id")
        safe_id = self._sanitize(str(session_id), "session_id")
        base = self.base_path / safe_tenant / safe_id
        if agent_id:
            safe_agent = self._sanitize(str(agent_id), "agent_id")
            return base / "agents" / safe_agent
        return base

    async def create_session(
        self,
        tenant_id: str,
        session_id: str,
        user_id: str,
    ) -> SessionInfo:
        """
        Create a new session directory with default files.

        Args:
            tenant_id: Tenant ID for isolation
            session_id: Unique session identifier
            user_id: User ID for ownership

        Returns:
            SessionInfo with session metadata

        Raises:
            ValueError: If session already exists or invalid session_id
        """
        session_path = self._get_session_path(tenant_id, session_id)

        if session_path.exists():
            raise ValueError(f"Session '{session_id}' already exists")

        # Create session directory structure
        await aiofiles.os.makedirs(session_path, exist_ok=True)
        await aiofiles.os.makedirs(session_path / "logs", exist_ok=True)

        # Create default files
        now = datetime.utcnow()
        state = json.loads(self._default_files["state.json"])
        state["created_at"] = now.isoformat()
        state["user_id"] = user_id

        await self.write(tenant_id, session_id, "plan.md", self._default_files["plan.md"])
        await self.write(tenant_id, session_id, "todo.md", self._default_files["todo.md"])
        await self.write(tenant_id, session_id, "state.json", json.dumps(state, indent=2))
        await self.write(tenant_id, session_id, "logs/history.md", f"# Session History\n\nStarted at: {now.isoformat()}\n\n")

        return SessionInfo(
            session_id=session_id,
            user_id=user_id,
            path=str(session_path),
            created_at=now,
            updated_at=now,
            file_count=4,
            status="active",
        )

    async def get_or_create_session(
        self,
        tenant_id: str,
        session_id: str,
        user_id: str = "unknown",
    ) -> SessionInfo:
        """
        Get existing session or create new one.

        Args:
            tenant_id: Tenant ID for isolation
            session_id: Session identifier
            user_id: User ID for creation if needed

        Returns:
            SessionInfo for session
        """
        try:
            return await self.get_session(tenant_id, session_id)
        except ValueError:
            return await self.create_session(tenant_id, session_id, user_id)

    async def get_session(self, tenant_id: str, session_id: str) -> SessionInfo:
        """
        Get session information.

        Args:
            tenant_id: Tenant ID for isolation
            session_id: Session identifier

        Returns:
            SessionInfo with session metadata

        Raises:
            ValueError: If session does not exist
        """
        session_path = self._get_session_path(tenant_id, session_id)

        if not session_path.exists():
            raise ValueError(f"Session '{session_id}' not found")

        # Calculate size and file count
        size_bytes = 0
        file_count = 0

        for file_path in session_path.rglob("*"):
            if file_path.is_file() and not file_path.is_symlink():
                size_bytes += file_path.stat().st_size
                file_count += 1

        # Read state.json for metadata
        state_content = await self.read(tenant_id, session_id, "state.json")
        state = json.loads(state_content) if state_content else {}

        # Get timestamps
        stat = session_path.stat()
        created_at = datetime.fromisoformat(state.get("created_at", stat.st_ctime.__str__()))
        updated_at = datetime.fromtimestamp(stat.st_mtime)

        return SessionInfo(
            session_id=session_id,
            user_id=state.get("user_id", "unknown"),
            path=str(session_path),
            created_at=created_at,
            updated_at=updated_at,
            file_count=file_count,
            size_bytes=size_bytes,
            status=state.get("status", "active"),
        )

    async def read(
        self, tenant_id: str, session_id: str, file_path: str,
        agent_id: Optional[str] = None,
    ) -> str:
        """
        Read a file from session storage.

        Args:
            tenant_id: Tenant ID for isolation
            session_id: Session identifier
            file_path: Relative path within session (e.g., "plan.md" or "logs/history.md")
            agent_id: Optional agent ID for per-agent isolation

        Returns:
            File content as string, or empty string if not found
        """
        session_path = self._get_session_path(tenant_id, session_id, agent_id)
        full_path = session_path / file_path

        if not full_path.exists():
            return ""

        # Security: prevent reading outside session directory
        try:
            full_path.resolve().relative_to(session_path.resolve())
        except ValueError:
            raise ValueError(f"Path traversal detected: {file_path}")

        async with aiofiles.open(full_path, "r", encoding="utf-8") as f:
            return await f.read()

    async def write(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
        content: str,
        agent_id: Optional[str] = None,
    ) -> None:
        """
        Write a file to session storage.

        DBOS automatically checkpoints after this operation when enabled.

        Args:
            tenant_id: Tenant ID for isolation
            session_id: Session identifier
            file_path: Relative path within session
            content: Content to write
            agent_id: Optional agent ID for per-agent isolation
        """
        session_path = self._get_session_path(tenant_id, session_id, agent_id)
        full_path = session_path / file_path

        # Security: prevent writing outside session directory
        try:
            full_path.resolve().relative_to(session_path.resolve())
        except ValueError:
            raise ValueError(f"Path traversal detected: {file_path}")

        # Create parent directories
        await aiofiles.os.makedirs(full_path.parent, exist_ok=True)

        async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
            await f.write(content)

        # Trigger DBOS checkpoint if enabled
        if self.checkpointing_enabled:
            await self._checkpoint(session_id, file_path)

    async def write_binary(
        self,
        tenant_id: str,
        session_id: str,
        path: str,
        data: bytes,
        agent_id: Optional[str] = None,
    ) -> str:
        """
        Write binary data to session storage.

        Used by SandboxExecutor to save sandbox outputs (charts, CSVs, etc.).

        Args:
            tenant_id: Tenant ID for isolation
            session_id: Session identifier
            path: Relative path within session
            data: Binary content to write
            agent_id: Optional agent ID for per-agent isolation

        Returns:
            Absolute path of the written file
        """
        session_path = self._get_session_path(tenant_id, session_id, agent_id)
        full_path = session_path / path

        # Security: prevent writing outside session directory
        try:
            full_path.resolve().relative_to(session_path.resolve())
        except ValueError:
            raise ValueError(f"Path traversal detected: {path}")

        await aiofiles.os.makedirs(full_path.parent, exist_ok=True)

        async with aiofiles.open(full_path, "wb") as f:
            await f.write(data)

        return str(full_path)

    async def read_binary(
        self,
        tenant_id: str,
        session_id: str,
        path: str,
        agent_id: Optional[str] = None,
    ) -> Optional[bytes]:
        """
        Read binary data from session storage.

        Args:
            tenant_id: Tenant ID for isolation
            session_id: Session identifier
            path: Relative path within session
            agent_id: Optional agent ID for per-agent isolation

        Returns:
            Binary content or None if file doesn't exist
        """
        session_path = self._get_session_path(tenant_id, session_id, agent_id)
        full_path = session_path / path

        # Security: prevent reading outside session directory
        try:
            full_path.resolve().relative_to(session_path.resolve())
        except ValueError:
            raise ValueError(f"Path traversal detected: {path}")

        if not full_path.is_file():
            return None

        async with aiofiles.open(full_path, "rb") as f:
            return await f.read()

    def get_screenshot_url(
        self,
        session_id: str,
        path: str,
    ) -> str:
        """
        Get a URL to serve a screenshot file from session workspace.

        Uses the existing workspace download endpoint. For local dev this
        returns a relative API path; for production it could return a
        presigned S3 URL (not yet implemented).

        Args:
            session_id: Session identifier
            path: Relative path within workspace (e.g. 'screenshots/w1_123.jpg')

        Returns:
            URL string for <img src> usage
        """
        # Use the existing deep work workspace download endpoint
        return f"/api/v1/deep-work/{session_id}/workspace/{path}"

    async def append(
        self,
        tenant_id: str,
        session_id: str,
        file_path: str,
        content: str,
        agent_id: Optional[str] = None,
    ) -> None:
        """
        Append content to a file in session storage.

        Args:
            tenant_id: Tenant ID for isolation
            session_id: Session identifier
            file_path: Relative path within session
            content: Content to append
            agent_id: Optional agent ID for per-agent isolation
        """
        session_path = self._get_session_path(tenant_id, session_id, agent_id)
        full_path = session_path / file_path

        # Security check
        try:
            full_path.resolve().relative_to(session_path.resolve())
        except ValueError:
            raise ValueError(f"Path traversal detected: {file_path}")

        # Create parent directories
        await aiofiles.os.makedirs(full_path.parent, exist_ok=True)

        async with aiofiles.open(full_path, "a", encoding="utf-8") as f:
            await f.write(content)

    async def delete_file(
        self, tenant_id: str, session_id: str, file_path: str,
        agent_id: Optional[str] = None,
    ) -> bool:
        """
        Delete a file from session storage.

        Args:
            tenant_id: Tenant ID for isolation
            session_id: Session identifier
            file_path: Relative path within session
            agent_id: Optional agent ID for per-agent isolation

        Returns:
            True if deleted, False if not found
        """
        session_path = self._get_session_path(tenant_id, session_id, agent_id)
        full_path = session_path / file_path

        # Security check
        try:
            full_path.resolve().relative_to(session_path.resolve())
        except ValueError:
            raise ValueError(f"Path traversal detected: {file_path}")

        if full_path.exists():
            await aiofiles.os.remove(full_path)
            return True

        return False

    async def list_files(
        self, tenant_id: str, session_id: str,
        agent_id: Optional[str] = None,
    ) -> list[str]:
        """
        List all files in a session.

        When agent_id is provided, lists only that agent's files.
        When omitted, lists session-level files including agents/ subdirectories
        (orchestrator/admin view).

        Args:
            tenant_id: Tenant ID for isolation
            session_id: Session identifier
            agent_id: Optional agent ID for per-agent isolation

        Returns:
            List of relative file paths
        """
        session_path = self._get_session_path(tenant_id, session_id, agent_id)

        if not session_path.exists():
            return []

        files = []
        for file_path in session_path.rglob("*"):
            if file_path.is_file() and not file_path.is_symlink():
                relative = file_path.relative_to(session_path)
                files.append(str(relative))

        return sorted(files)

    async def delete_session(self, tenant_id: str, session_id: str) -> bool:
        """
        Delete an entire session directory.

        Args:
            tenant_id: Tenant ID for isolation
            session_id: Session identifier

        Returns:
            True if deleted, False if not found
        """
        session_path = self._get_session_path(tenant_id, session_id)

        if session_path.exists():
            await asyncio.to_thread(shutil.rmtree, session_path)
            return True

        return False

    async def list_sessions(self, tenant_id: str) -> list[SessionInfo]:
        """
        List all sessions for a tenant.

        Args:
            tenant_id: Tenant ID for isolation

        Returns:
            List of SessionInfo objects
        """
        sessions = []
        safe_tenant = self._sanitize(str(tenant_id), "tenant_id")
        tenant_path = self.base_path / safe_tenant

        if not tenant_path.exists():
            return sessions

        for session_dir in tenant_path.iterdir():
            if session_dir.is_dir() and not session_dir.name.startswith("."):
                try:
                    info = await self.get_session(tenant_id, session_dir.name)
                    sessions.append(info)
                except (ValueError, json.JSONDecodeError):
                    # Skip invalid sessions
                    pass

        return sorted(sessions, key=lambda s: s.created_at, reverse=True)

    async def get_recitation_context(self, tenant_id: str, session_id: str) -> str:
        """
        Get plan.md and todo.md for recitation pattern.

        The recitation pattern (from Manus AI research) achieves 42% error reduction by:
        1. Including high-level plan in every agent turn
        2. Including current todo list in every agent turn
        3. Keeping agents focused on original goals during long sessions

        Args:
            tenant_id: Tenant ID for isolation
            session_id: Session identifier

        Returns:
            Formatted context for agent system prompt
        """
        plan = await self.read(tenant_id, session_id, "plan.md")
        todo = await self.read(tenant_id, session_id, "todo.md")

        # Skip recitation when both files are still their default templates
        from app.services.session_memory import _is_default_content
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
- Save outputs to /artifacts/{session_id}/
"""

    async def update_state(
        self,
        tenant_id: str,
        session_id: str,
        updates: dict,
    ) -> dict:
        """
        Update session state.json with new values.

        Args:
            tenant_id: Tenant ID for isolation
            session_id: Session identifier
            updates: Dictionary of updates to merge into state

        Returns:
            Updated state dictionary
        """
        state_content = await self.read(tenant_id, session_id, "state.json")
        state = json.loads(state_content) if state_content else {}

        state.update(updates)
        state["updated_at"] = datetime.utcnow().isoformat()

        await self.write(tenant_id, session_id, "state.json", json.dumps(state, indent=2))

        return state

    async def log_action(
        self,
        tenant_id: str,
        session_id: str,
        action: str,
        details: Optional[str] = None,
    ) -> None:
        """
        Append an action to the session's history log.

        Args:
            tenant_id: Tenant ID for isolation
            session_id: Session identifier
            action: Action description
            details: Optional additional details
        """
        timestamp = datetime.utcnow().isoformat()
        entry = f"\n## {timestamp}\n\n**Action:** {action}\n"

        if details:
            entry += f"\n{details}\n"

        await self.append(tenant_id, session_id, "logs/history.md", entry)

    async def log_error(
        self,
        tenant_id: str,
        session_id: str,
        error: str,
        traceback: Optional[str] = None,
    ) -> None:
        """
        Log an error to the session's error log.

        Args:
            tenant_id: Tenant ID for isolation
            session_id: Session identifier
            error: Error message
            traceback: Optional traceback string
        """
        timestamp = datetime.utcnow().isoformat()
        entry = f"[{timestamp}] ERROR: {error}\n"

        if traceback:
            entry += f"{traceback}\n"

        await self.append(tenant_id, session_id, "logs/errors.log", entry)

    async def _checkpoint(self, session_id: str, file_path: str) -> None:
        """
        Trigger DBOS checkpoint after a write operation.

        This is a hook for integration with the DurableWorkflowEngine.
        Currently a no-op until DBOS integration is complete.
        """
        # TODO: Integrate with DBOS workflow checkpointing
        # from app.services.durable_workflow_engine import DurableWorkflowEngine
        # engine = DurableWorkflowEngine.get_instance()
        # await engine.checkpoint_session(session_id, file_path)
        pass


# ============================================================================
# Session Lifecycle Cleanup (Phase B)
# ============================================================================

async def cleanup_stale_sessions(
    db=None,
    retention_days: int = 30,
    batch_size: int = 50,
) -> dict:
    """Delete sessions older than retention_days.

    A session is safe to delete only if:
    1. All files have mtime older than retention_days (uses max file mtime, not dir mtime)
    2. state.json status is 'completed' or 'failed' (or state.json doesn't exist)
    3. No BackgroundJob references this session with PENDING/RUNNING status

    Args:
        db: Optional database session for checking active background jobs.
        retention_days: Delete sessions inactive for this many days.
        batch_size: Max sessions to delete per run.

    Returns:
        Summary dict with deleted, skipped, and error counts.
    """
    import logging
    from datetime import datetime, timedelta

    logger = logging.getLogger(__name__)
    storage = get_session_storage()
    settings = get_settings()
    cutoff = datetime.now() - timedelta(days=retention_days)

    # list_sessions() requires a tenant_id; iterate over all tenant dirs
    all_sessions = []
    if storage.base_path.exists():
        for tenant_dir in storage.base_path.iterdir():
            if tenant_dir.is_dir() and not tenant_dir.name.startswith("."):
                try:
                    tenant_sessions = await storage.list_sessions(tenant_dir.name)
                    all_sessions.extend(tenant_sessions)
                except Exception as e:
                    logger.warning(f"Failed to list sessions for tenant {tenant_dir.name}: {e}")
    deleted = 0
    skipped = 0
    errors = 0

    for session_info in all_sessions[:batch_size + skipped]:
        if deleted >= batch_size:
            break

        session_id = session_info.session_id
        session_path = Path(session_info.path)

        try:
            # 1. Check last activity using max mtime of all files (not directory mtime)
            max_mtime = 0.0
            for f in session_path.rglob("*"):
                if f.is_file():
                    try:
                        max_mtime = max(max_mtime, f.stat().st_mtime)
                    except OSError:
                        pass
            if max_mtime == 0.0:
                # No files — use dir mtime as fallback
                max_mtime = session_path.stat().st_mtime

            last_activity = datetime.fromtimestamp(max_mtime)
            if last_activity > cutoff:
                skipped += 1
                continue

            # 2. Check state.json status
            state_file = session_path / "state.json"
            if state_file.exists():
                try:
                    state = json.loads(state_file.read_text())
                    status = state.get("status", "")
                    if status not in ("completed", "failed", ""):
                        skipped += 1
                        continue
                except (json.JSONDecodeError, OSError):
                    pass  # Corrupted state.json — safe to delete

            # 3. Check for active background jobs referencing this session
            if db is not None:
                try:
                    from sqlalchemy import select, or_
                    from app.models.background_job import BackgroundJob, JobStatus

                    result = await db.execute(
                        select(BackgroundJob.id)
                        .where(
                            BackgroundJob.session_id == session_id,
                            or_(
                                BackgroundJob.status == JobStatus.PENDING.value,
                                BackgroundJob.status == JobStatus.RUNNING.value,
                            ),
                        )
                        .limit(1)
                    )
                    if result.scalar_one_or_none() is not None:
                        skipped += 1
                        continue
                except Exception as e:
                    logger.warning(f"DB check for session {session_id} failed: {e}")
                    skipped += 1
                    continue

            # 4. Delete the session directory
            await asyncio.to_thread(shutil.rmtree, session_path)
            deleted += 1
            logger.info(f"Deleted stale session: {session_id} (last activity: {last_activity})")

        except Exception as e:
            errors += 1
            logger.warning(f"Error cleaning up session {session_id}: {e}")

    summary = {"deleted": deleted, "skipped": skipped, "errors": errors}
    logger.info(f"Session cleanup complete: {summary}")
    return summary


# ============================================================================
# Singleton and Dependency Injection
# ============================================================================

_session_storage: Optional[SessionStorage] = None


def get_session_storage() -> SessionStorage:
    """Get or create the singleton SessionStorage instance."""
    global _session_storage
    if _session_storage is None:
        _session_storage = SessionStorage()
    return _session_storage


def reset_session_storage() -> None:
    """Reset the singleton instance (for testing)."""
    global _session_storage
    _session_storage = None


# ============================================================================
# Backward Compatibility with InMemorySessionMemory API
# ============================================================================

class FilesystemSessionMemory:
    """
    Filesystem-based session storage with InMemorySessionMemory-compatible API.

    This class provides backward compatibility with the existing InMemorySessionMemory
    interface while using the new filesystem-based SessionStorage implementation.

    Usage:
        memory = FilesystemSessionMemory.get_instance()
        await memory.write(session_id, "plan.md", "# My Plan...")
        content = await memory.read(session_id, "plan.md")
    """

    _instance: Optional['FilesystemSessionMemory'] = None

    def __init__(self):
        self._storage = SessionStorage()

    @classmethod
    def get_instance(cls) -> 'FilesystemSessionMemory':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset for testing."""
        cls._instance = None

    async def read(
        self,
        tenant_id: str,
        session_id: str,
        file_name: str,
        user_id: str = "unknown",
    ) -> str:
        """Read a file from session storage."""
        await self._storage.get_or_create_session(tenant_id, session_id, user_id)
        return await self._storage.read(tenant_id, session_id, file_name)

    async def write(
        self,
        tenant_id: str,
        session_id: str,
        file_name: str,
        content: str,
        user_id: str = "unknown",
    ) -> None:
        """Write a file to session storage."""
        await self._storage.get_or_create_session(tenant_id, session_id, user_id)
        await self._storage.write(tenant_id, session_id, file_name, content)

    async def append(
        self,
        tenant_id: str,
        session_id: str,
        file_name: str,
        content: str,
        user_id: str = "unknown",
    ) -> None:
        """Append content to a file."""
        await self._storage.get_or_create_session(tenant_id, session_id, user_id)
        await self._storage.append(tenant_id, session_id, file_name, content)

    async def list_files(self, tenant_id: str, session_id: str) -> list[str]:
        """List all files in a session."""
        return await self._storage.list_files(tenant_id, session_id)

    async def delete_file(self, tenant_id: str, session_id: str, file_name: str) -> bool:
        """Delete a file from session storage."""
        return await self._storage.delete_file(tenant_id, session_id, file_name)

    async def delete_session(self, tenant_id: str, session_id: str) -> bool:
        """Delete an entire session."""
        return await self._storage.delete_session(tenant_id, session_id)

    async def get_session_info(self, tenant_id: str, session_id: str) -> Optional[dict]:
        """Get session metadata."""
        try:
            info = await self._storage.get_session(tenant_id, session_id)
            return {
                "session_id": info.session_id,
                "user_id": info.user_id,
                "created_at": info.created_at.isoformat(),
                "updated_at": info.updated_at.isoformat(),
                "file_count": info.file_count,
                "files": await self._storage.list_files(tenant_id, session_id),
            }
        except ValueError:
            return None
