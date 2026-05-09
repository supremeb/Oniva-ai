"""
Task Manager Skills
Session-scoped task tracking: create, update, list tasks stored as JSON.
Replaces the fragile update_todo markdown approach.
"""

import asyncio
import json
import logging
from typing import Any, Literal

from app.agents.skills.context import SkillContext
from app.agents.skills.registry import skill
from app.schemas.task import Task

logger = logging.getLogger(__name__)

# Per-session locks for concurrent JSON file access
_session_locks: dict[str, asyncio.Lock] = {}


def _get_lock(session_id: str) -> asyncio.Lock:
    if session_id not in _session_locks:
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]


async def _load_tasks(ctx: SkillContext) -> list[Task]:
    from app.services.session_storage import get_session_storage

    storage = get_session_storage()
    content = await storage.read(str(ctx.tenant_id), ctx.session_id, "tasks.json")
    if not content:
        return []
    return [Task(**t) for t in json.loads(content)]


async def _save_tasks(ctx: SkillContext, tasks: list[Task]) -> None:
    from app.services.session_storage import get_session_storage

    storage = get_session_storage()
    await storage.write(
        str(ctx.tenant_id),
        ctx.session_id,
        "tasks.json",
        json.dumps([t.model_dump() for t in tasks], indent=2),
    )


@skill(name="create_task", description="Create a new task to track work.", category="planning")
async def create_task(
    ctx: SkillContext,
    title: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a new task with a unique ID.

    Args:
        title: Short description of the task.
        description: Optional detailed description.
    """
    if ctx.session_id is None:
        return {"success": False, "error": "No active session."}

    async with _get_lock(str(ctx.session_id)):
        tasks = await _load_tasks(ctx)
        task = Task(title=title, description=description)
        tasks.append(task)
        await _save_tasks(ctx, tasks)
    return {"success": True, "type": "task_update", "action": "created", "task": task.model_dump()}


@skill(name="update_task", description="Update a task's status.", category="planning")
async def update_task(
    ctx: SkillContext,
    task_id: str,
    status: Literal["pending", "in_progress", "completed"] | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Update a task's status or title.

    Args:
        task_id: ID of the task to update.
        status: New status (pending, in_progress, completed).
        title: New title for the task.
    """
    if ctx.session_id is None:
        return {"success": False, "error": "No active session."}

    async with _get_lock(str(ctx.session_id)):
        tasks = await _load_tasks(ctx)
        task = next((t for t in tasks if t.id == task_id), None)
        if not task:
            return {"success": False, "error": f"Task {task_id} not found"}

        if status:
            if task.status == "completed":
                return {"success": False, "error": "Cannot modify completed task."}
            task.status = status
        if title:
            task.title = title

        await _save_tasks(ctx, tasks)
    return {"success": True, "type": "task_update", "action": "updated", "task": task.model_dump()}


@skill(name="list_tasks", description="List all tasks in the current session.", category="planning")
async def list_tasks(
    ctx: SkillContext,
    status_filter: str | None = None,
) -> dict[str, Any]:
    """List tasks, optionally filtered by status.

    Args:
        status_filter: Filter by status (pending, in_progress, completed, or all).
    """
    if ctx.session_id is None:
        return {"success": False, "error": "No active session."}

    tasks = await _load_tasks(ctx)
    if status_filter and status_filter != "all":
        filtered = [t for t in tasks if t.status == status_filter]
    else:
        filtered = tasks

    summary = {
        "total": len(tasks),
        "pending": sum(1 for t in tasks if t.status == "pending"),
        "in_progress": sum(1 for t in tasks if t.status == "in_progress"),
        "completed": sum(1 for t in tasks if t.status == "completed"),
    }
    return {"success": True, "tasks": [t.model_dump() for t in filtered], "summary": summary}
