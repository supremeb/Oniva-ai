"""
Delete File Skill
Delete a file from the agent's session workspace.
"""

from pathlib import Path
from typing import Any, Dict

from app.agents.skills.context import SkillContext
from app.agents.skills.registry import skill


def _validate_workspace_path(file_path: str) -> str:
    """Ensure file_path is a clean relative path with no escape sequences."""
    normalized = Path(file_path)
    if normalized.is_absolute():
        raise ValueError(f"Absolute paths not allowed: {file_path}")
    if ".." in normalized.parts:
        raise ValueError(f"Path traversal not allowed: {file_path}")
    return str(normalized)


@skill(
    name="delete_file",
    description="Delete a file from the agent's session workspace.",
    category="file",
    version="2.0.0",
    author="oniva",
)
async def delete_file(
    ctx: SkillContext,
    file_path: str,
) -> Dict[str, Any]:
    """
    Delete a file from the agent's session workspace.

    Args:
        ctx: Skill execution context
        file_path: Relative path within workspace (e.g., "data/old_results.json")

    Returns:
        Dict with success status and deleted flag
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

    try:
        from app.services.session_storage import get_session_storage

        storage = get_session_storage()
        deleted = await storage.delete_file(str(ctx.tenant_id), ctx.session_id, f"workspace/{clean_path}")

        return {
            "success": True,
            "deleted": deleted,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
