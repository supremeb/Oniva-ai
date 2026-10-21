"""Project memory gateway tools (evals/context/memory plan, U26 / KTD17).

Five single-purpose tools over ``app.services.project_memory`` (``memory_view``, ``memory_create``,
``memory_str_replace``, ``memory_insert``, ``memory_delete``). Command semantics mirror the published
memory-tool interface; no code is copied.

  * The project comes from the call binding (``ctx.metadata["project_id"]``), never from arguments, and
    every query also filters by tenant (RLS on top). Each call opens its own tenant-scoped session.
  * Pending rows are invisible here (``include_pending`` is never passed).
  * A tainted run cannot reach a write without an approval (Rule of Two, ``tool_specs``); an approved
    write is a normal ``source_kind="model"`` row with ``status="active"``.
  * Writes echo the new version and a short diff, never the full content.
  * With ``ENABLE_PROJECT_MEMORY`` off for the tenant a call is refused, and the agent builders do not
    grant the tools at all (U28), so the model never sees them.
"""
import difflib
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.agents.skills.context import SkillContext
from app.agents.skills.registry import skill
from app.core.database import get_db_for_tenant
from app.internal_api.execution import ToolOutcome
from app.models.tenant import Tenant
from app.services import project_memory as store
from app.services.feature_flags import get_feature
from app.services.project_memory import MemoryStoreError

logger = logging.getLogger(__name__)

FLAG = "ENABLE_PROJECT_MEMORY"
MAX_DIFF_LINES = 24
MAX_DIFF_CHARS = 2000


def state_factory(tenant_id: Any):
    """A tenant-scoped session per call (patched in tests, like ``engine_tools.state_factory``)."""
    return lambda: get_db_for_tenant(str(tenant_id))


def _err(message: str, code: str = "rejected") -> ToolOutcome:
    return ToolOutcome(content=message, is_error=True, details={"error": code})


def _ok(payload: Dict[str, Any]) -> ToolOutcome:
    return ToolOutcome(content=json.dumps(payload, ensure_ascii=False), is_error=False, details={})


def _short_diff(old: str, new: str, path: str) -> str:
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{path}", f"b/{path}", lineterm="", n=1))
    clipped = len(lines) > MAX_DIFF_LINES
    text = "\n".join(lines[:MAX_DIFF_LINES])
    if len(text) > MAX_DIFF_CHARS:
        text, clipped = text[:MAX_DIFF_CHARS], True
    return text + ("\n[diff truncated]" if clipped else "")


def _scope(ctx: SkillContext):
    project_id = ctx.metadata.get("project_id")
    if not ctx.tenant_id or not project_id:
        raise MemoryStoreError("no_project", "Project memory needs a project; this conversation has none.")
    return uuid.UUID(str(ctx.tenant_id)), uuid.UUID(str(project_id))


async def _run(ctx: SkillContext, op) -> ToolOutcome:
    """Open the session, gate on the flag, run ``op(db, tenant_id, project_id)``, map store errors."""
    try:
        tenant_id, project_id = _scope(ctx)
        async with state_factory(tenant_id)() as db:
            tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
            if tenant is None or not get_feature(tenant, FLAG):
                return _err("Project memory is not enabled.", "memory_disabled")
            return await op(db, tenant_id, project_id)
    except MemoryStoreError as exc:
        return _err(exc.message, exc.code)


def _conversation_id(ctx: SkillContext):
    raw = ctx.metadata.get("conversation_id")
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


async def _content(db, tenant_id, project_id, path: str) -> str:
    row = await store._get(db, tenant_id=tenant_id, project_id=project_id, user_id=None, path=path,
                           include_pending=False)
    return row.content if row is not None else ""


@skill(
    name="memory_view",
    description=(
        "View the project's memory. Without a path, lists the memory files (path, version, size). With a path, "
        "returns that file, optionally only the 1-based inclusive line range [start, end]. "
        "playbook.md is a generated, read-only file of the most useful lessons. "
        'Example: {"path": "core.md"}'
    ),
    category="memory",
    author="oniva",
)
async def memory_view(ctx: SkillContext, path: Optional[str] = None, view_range: Optional[List[int]] = None) -> ToolOutcome:
    """
    Read project memory.

    Args:
        path: Memory file such as core.md or notes/topic.md. Omit to list all files.
        view_range: Optional [start_line, end_line] (1-based, inclusive) to read part of the file.
    """
    if view_range is not None and (len(view_range) != 2 or not all(isinstance(n, int) for n in view_range)):
        return _err("view_range must be [start_line, end_line] as two integers.", "invalid_argument")

    async def op(db, tenant_id, project_id):
        result = await store.view(
            db, tenant_id=tenant_id, project_id=project_id, path=path,
            view_range=tuple(view_range) if view_range else None,
        )
        return _ok(result)

    return await _run(ctx, op)


@skill(
    name="memory_create",
    description=(
        "Create a memory file, or replace its whole content when the path exists. Keep memory short and durable: "
        "facts, preferences and decisions worth knowing in later conversations. core.md is shown to you at the start "
        "of every conversation. Paths end in .md or .txt; playbook.md cannot be written. "
        'Example: {"path": "core.md", "file_text": "# Project\\n- Reports use APA citations."}'
    ),
    category="memory",
    author="oniva",
)
async def memory_create(ctx: SkillContext, path: str, file_text: str) -> ToolOutcome:
    """
    Create or replace a memory file.

    Args:
        path: Relative path ending in .md or .txt, for example core.md or notes/sources.md.
        file_text: The full content of the file.
    """
    async def op(db, tenant_id, project_id):
        old = await _content(db, tenant_id, project_id, path)
        row = await store.create(
            db, tenant_id=tenant_id, project_id=project_id, path=path, content=file_text,
            source_kind="model", source_conversation_id=_conversation_id(ctx), status="active",
        )
        return _ok({"path": row.path, "version": row.version, "diff": _short_diff(old, row.content, row.path)})

    return await _run(ctx, op)


@skill(
    name="memory_str_replace",
    description=(
        "Replace one exact piece of text in a memory file. old_str must appear exactly once; include surrounding "
        "context if it is ambiguous. "
        'Example: {"path": "core.md", "old_str": "APA", "new_str": "Chicago"}'
    ),
    category="memory",
    author="oniva",
)
async def memory_str_replace(ctx: SkillContext, path: str, old_str: str, new_str: str) -> ToolOutcome:
    """
    Replace text in a memory file.

    Args:
        path: The memory file.
        old_str: Exact text to replace; must match exactly once.
        new_str: Replacement text (may be empty to delete the old text).
    """
    async def op(db, tenant_id, project_id):
        old = await _content(db, tenant_id, project_id, path)
        row = await store.str_replace(db, tenant_id=tenant_id, project_id=project_id, path=path,
                                      old=old_str, new=new_str)
        return _ok({"path": row.path, "version": row.version, "diff": _short_diff(old, row.content, row.path)})

    return await _run(ctx, op)


@skill(
    name="memory_insert",
    description=(
        "Insert text into a memory file so that it becomes the given 1-based line (one past the last line appends). "
        'Example: {"path": "core.md", "insert_line": 3, "insert_text": "- Prefer short answers."}'
    ),
    category="memory",
    author="oniva",
)
async def memory_insert(ctx: SkillContext, path: str, insert_line: int, insert_text: str) -> ToolOutcome:
    """
    Insert text at a line of a memory file.

    Args:
        path: The memory file.
        insert_line: 1-based line number the new text will occupy.
        insert_text: The text to insert.
    """
    async def op(db, tenant_id, project_id):
        old = await _content(db, tenant_id, project_id, path)
        row = await store.insert(db, tenant_id=tenant_id, project_id=project_id, path=path,
                                 line=insert_line, text=insert_text)
        return _ok({"path": row.path, "version": row.version, "diff": _short_diff(old, row.content, row.path)})

    return await _run(ctx, op)


@skill(
    name="memory_delete",
    description=(
        "Delete a memory file. This cannot be undone. "
        'Example: {"path": "notes/old.md"}'
    ),
    category="memory",
    author="oniva",
)
async def memory_delete(ctx: SkillContext, path: str) -> ToolOutcome:
    """
    Delete a memory file.

    Args:
        path: The memory file to delete.
    """
    async def op(db, tenant_id, project_id):
        await store.delete(db, tenant_id=tenant_id, project_id=project_id, path=path)
        return _ok({"path": path, "deleted": True})

    return await _run(ctx, op)
