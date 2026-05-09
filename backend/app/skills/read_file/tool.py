"""
Read File Skill
Read text content from the agent's session workspace.
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
    name="read_file",
    description="Read text content from the agent's session workspace.",
    category="file",
    version="2.0.0",
    author="oniva",
)
async def read_file(
    ctx: SkillContext,
    file_path: str,
) -> Dict[str, Any]:
    """
    Read text content from the agent's session workspace.

    Args:
        ctx: Skill execution context
        file_path: Relative path within workspace (e.g., "data/results.json")

    Returns:
        Dict with success status, content, and size_bytes
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
        workspace_path = f"workspace/{clean_path}"

        # Check file exists (SessionStorage.read returns "" for missing files)
        session_path = storage._get_session_path(str(ctx.tenant_id), ctx.session_id)
        full_path = session_path / workspace_path
        if not full_path.exists():
            return {
                "success": False,
                "error": f"File not found: {clean_path}",
            }

        content = await storage.read(str(ctx.tenant_id), ctx.session_id, workspace_path)
        size_bytes = len(content.encode("utf-8"))

        return {
            "success": True,
            "content": content,
            "size_bytes": size_bytes,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
