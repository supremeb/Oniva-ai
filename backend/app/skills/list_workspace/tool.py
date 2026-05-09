"""
List Workspace Skill
List files and directories in the agent's session workspace.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from app.agents.skills.context import SkillContext
from app.agents.skills.registry import skill

MAX_ENTRIES = 50


def _validate_workspace_path(file_path: str) -> str:
    """Ensure file_path is a clean relative path with no escape sequences."""
    normalized = Path(file_path)
    if normalized.is_absolute():
        raise ValueError(f"Absolute paths not allowed: {file_path}")
    if ".." in normalized.parts:
        raise ValueError(f"Path traversal not allowed: {file_path}")
    return str(normalized)


@skill(
    name="list_workspace",
    description="List files and directories in the agent's session workspace with metadata.",
    category="file",
    version="2.0.0",
    author="oniva",
)
async def list_workspace(
    ctx: SkillContext,
    path: str = "",
) -> Dict[str, Any]:
    """
    List files and directories in the agent's session workspace.

    Args:
        ctx: Skill execution context
        path: Relative subdirectory to list (default: workspace root)

    Returns:
        Dict with files list, total_count, and truncated flag
    """
    if ctx.session_id is None:
        return {
            "success": False,
            "error": "Workspace requires an active session. No session_id found in context.",
        }

    try:
        if path:
            clean_path = _validate_workspace_path(path)
            workspace_path = f"workspace/{clean_path}"
        else:
            workspace_path = "workspace"
    except ValueError as e:
        return {"success": False, "error": str(e)}

    try:
        from app.services.session_storage import get_session_storage

        storage = get_session_storage()
        session_path = storage._get_session_path(str(ctx.tenant_id), ctx.session_id)
        target_dir = session_path / workspace_path

        if not target_dir.exists() or not target_dir.is_dir():
            return {
                "success": True,
                "files": [],
                "total_count": 0,
                "truncated": False,
            }

        entries: List[Dict[str, Any]] = []
        total_count = 0

        for entry in os.scandir(target_dir):
            total_count += 1
            if len(entries) < MAX_ENTRIES:
                stat = entry.stat()
                item: Dict[str, Any] = {
                    "name": entry.name,
                    "type": "directory" if entry.is_dir() else "file",
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
                if entry.is_file():
                    item["size"] = stat.st_size
                entries.append(item)

        return {
            "success": True,
            "files": entries,
            "total_count": total_count,
            "truncated": total_count > MAX_ENTRIES,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
