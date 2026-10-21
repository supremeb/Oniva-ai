"""
Read File Skill
Read text content from the agent's session workspace.
"""

from pathlib import Path
from typing import Any, Dict, Optional

from app.agents.skills.context import SkillContext
from app.agents.skills.registry import skill
from app.services import conversation_workspace as cw

DEFAULT_LIMIT_LINES = 200
MAX_PAGE_CHARS = 20_000


def _validate_workspace_path(file_path: str) -> str:
    """Ensure file_path is a clean relative path with no escape sequences."""
    normalized = Path(file_path)
    if normalized.is_absolute():
        raise ValueError(f"Absolute paths not allowed: {file_path}")
    if ".." in normalized.parts:
        raise ValueError(f"Path traversal not allowed: {file_path}")
    return str(normalized)


def _page(text: str, offset: int, limit: Optional[int], cap_chars: bool) -> Dict[str, Any]:
    lines = text.splitlines(keepends=True)
    total = len(lines)
    start = min(offset, total)
    end = total if limit is None else min(start + limit, total)
    chunk = lines[start:end]
    content = "".join(chunk)
    if cap_chars and len(content) > MAX_PAGE_CHARS:
        kept, size = [], 0
        for line in chunk:
            if size + len(line) > MAX_PAGE_CHARS:
                break
            kept.append(line)
            size += len(line)
        if not kept:  # one line longer than the cap: clip it and move past it
            kept, size = [chunk[0][:MAX_PAGE_CHARS]], MAX_PAGE_CHARS
        chunk = kept
        content = "".join(chunk)
        end = start + len(chunk)
    result: Dict[str, Any] = {
        "success": True,
        "content": content,
        "size_bytes": len(text.encode("utf-8")),
        "offset": start,
        "total_lines": total,
        "next_offset": end if end < total else None,
    }
    if end < total:
        result["note"] = (
            f"Showing lines {start}-{end} of {total}. "
            f"Call read_file again with offset={end} to read the next part."
        )
    return result


@skill(
    name="read_file",
    description=(
        "Read text content from the agent's session workspace, or a saved tool result "
        "(tool_results/<id>.txt) or archived history (conversation_history/<id>.md). "
        "Use offset and limit (in lines) to page through a large file."
    ),
    category="file",
    version="2.1.0",
    author="oniva",
)
async def read_file(
    ctx: SkillContext,
    file_path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Read text content from the agent's session workspace.

    Args:
        ctx: Skill execution context
        file_path: Relative path within workspace (e.g., "data/results.json"), or a path under
            ``tool_results/`` or ``conversation_history/`` (shared by the whole conversation)
        offset: First line to return (0-based). Optional; omitting both ``offset`` and ``limit``
            returns a workspace file whole, as before.
        limit: Maximum number of lines to return.

    Returns:
        Dict with success status, content, and size_bytes. When paged, also ``offset``,
        ``total_lines`` and ``next_offset`` (null at the end).
    """
    if offset is not None and offset < 0:
        return {"success": False, "error": "offset must be >= 0"}
    if limit is not None and limit < 1:
        return {"success": False, "error": "limit must be >= 1"}

    try:
        clean_path = _validate_workspace_path(file_path)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    if cw.is_conversation_path(clean_path):
        conversation_id = ctx.metadata.get("conversation_id")
        if ctx.tenant_id is None or not conversation_id:
            return {"success": False, "error": "Conversation files require an active conversation."}
        try:
            text = await cw.read(str(ctx.tenant_id), conversation_id, clean_path)
        except cw.WorkspaceError as e:
            return {"success": False, "error": str(e)}
        if text is None:
            return {"success": False, "error": f"File not found: {clean_path}"}
        return _page(text, offset or 0, limit if limit is not None else DEFAULT_LIMIT_LINES, cap_chars=limit is None)

    if ctx.session_id is None:
        return {
            "success": False,
            "error": "Workspace requires an active session. No session_id found in context.",
        }

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
        if offset is not None or limit is not None:
            return _page(content, offset or 0, limit, cap_chars=False)
        size_bytes = len(content.encode("utf-8"))

        return {
            "success": True,
            "content": content,
            "size_bytes": size_bytes,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
