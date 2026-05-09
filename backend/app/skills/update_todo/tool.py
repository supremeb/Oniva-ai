"""
Update Todo Skill
Agent marks todo items done, adds new items, during autonomous execution.
"""

from typing import Any, Dict, List, Optional

from app.agents.skills.context import SkillContext
from app.agents.skills.registry import skill


@skill(
    name="update_todo",
    description="Update the todo list during autonomous execution. "
    "Mark items as completed, add new items, or update item status.",
    category="planning",
    version="1.0.0",
    author="oniva",
)
async def update_todo(
    ctx: SkillContext,
    completed: Optional[List[str]] = None,
    add: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Update todo.md in the session workspace.

    Args:
        ctx: Skill execution context
        completed: List of todo item texts to mark as completed
        add: List of new todo items to add

    Returns:
        Dict with updated todo list
    """
    if ctx.session_id is None:
        return {
            "success": False,
            "error": "No active session. Cannot update todo.",
        }

    completed = completed or []
    add = add or []

    if not completed and not add:
        return {
            "success": False,
            "error": "Must specify items to complete or add.",
        }

    try:
        from app.services.session_storage import get_session_storage

        storage = get_session_storage()
        todo_content = await storage.read(str(ctx.tenant_id), ctx.session_id, "todo.md") or ""

        # Parse existing todos
        todos: List[Dict[str, str]] = []
        for line in todo_content.strip().split("\n"):
            line = line.strip()
            if line.startswith("- [x] "):
                todos.append({"text": line[6:], "status": "completed"})
            elif line.startswith("- [ ] "):
                todos.append({"text": line[6:], "status": "pending"})
            elif line.startswith("- [~] "):
                todos.append({"text": line[6:], "status": "in_progress"})

        # Mark items as completed
        completed_lower = {c.lower().strip() for c in completed}
        for todo in todos:
            if todo["text"].lower().strip() in completed_lower:
                todo["status"] = "completed"

        # Add new items
        for item in add:
            text = item.strip()
            if text:
                todos.append({"text": text, "status": "pending"})

        # Write back
        lines = []
        for todo in todos:
            if todo["status"] == "completed":
                lines.append(f"- [x] {todo['text']}")
            elif todo["status"] == "in_progress":
                lines.append(f"- [~] {todo['text']}")
            else:
                lines.append(f"- [ ] {todo['text']}")

        await storage.write(str(ctx.tenant_id), ctx.session_id, "todo.md", "\n".join(lines))

        return {
            "success": True,
            "todos": [
                {"text": t["text"], "status": t["status"]}
                for t in todos
            ],
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
