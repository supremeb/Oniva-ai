"""
Write File Skill
Write text content to the agent's session workspace.
"""

from pathlib import Path
from typing import Any, Dict

from app.agents.skills.context import SkillContext
from app.agents.skills.registry import skill

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def _validate_workspace_path(file_path: str) -> str:
    """Ensure file_path is a clean relative path with no escape sequences."""
    normalized = Path(file_path)
    if normalized.is_absolute():
        raise ValueError(f"Absolute paths not allowed: {file_path}")
    if ".." in normalized.parts:
        raise ValueError(f"Path traversal not allowed: {file_path}")
    return str(normalized)


@skill(
    name="write_file",
    description="Write text content to the agent's session workspace for persistent storage across tool calls.",
    category="file",
    version="2.0.0",
    author="oniva",
)
async def write_file(
    ctx: SkillContext,
    file_path: str,
    content: str,
) -> Dict[str, Any]:
    """
    Write text content to the agent's session workspace.

    Args:
        ctx: Skill execution context
        file_path: Relative path within workspace (e.g., "data/results.json")
        content: Text content to write

    Returns:
        Dict with success status, path, and size_bytes
    """
    if ctx.session_id is None:
        return {
            "success": False,
            "error": "Workspace requires an active session. No session_id found in context.",
        }

    try:
        clean_path = _validate_workspace_path(file_path)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    size_bytes = len(content.encode("utf-8"))
    if size_bytes > MAX_FILE_SIZE:
        return {
            "success": False,
            "error": f"File too large ({size_bytes} bytes). Maximum is {MAX_FILE_SIZE} bytes (10MB).",
        }

    try:
        from app.services.session_storage import get_session_storage

        storage = get_session_storage()
        await storage.write(str(ctx.tenant_id), ctx.session_id, f"workspace/{clean_path}", content)

        return {
            "success": True,
            "path": clean_path,
            "size_bytes": size_bytes,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
