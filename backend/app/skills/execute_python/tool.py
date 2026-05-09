"""
Execute Python Skill (E2B Sandbox)

Executes Python code in an E2B Firecracker microVM with hypervisor-level isolation.
Requires human review approval before execution.

When ENABLE_SANDBOX is False, falls back to the legacy in-process sandbox
for backward compatibility.

Timeout: 60 seconds (E2B remote), vs 5 seconds (legacy in-process).
"""

import logging
from typing import Any, Dict

from app.agents.skills.context import SkillContext
from app.config import get_settings

logger = logging.getLogger(__name__)


async def execute_python(
    ctx: SkillContext,
    code: str,
    input_files: list[str] | None = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    """
    Execute Python code in an E2B Firecracker sandbox.

    When ENABLE_SANDBOX is True and E2B_API_KEY is configured, creates a
    human review ticket and returns pending_review status. The node executor
    handles the review polling loop and sandbox execution on approval.

    When ENABLE_SANDBOX is False, falls back to the legacy in-process sandbox.

    Args:
        ctx: Skill execution context (must have session_id).
        code: Python code to execute.
        input_files: Optional workspace files to upload to sandbox.
        timeout: Maximum execution time in seconds (default: 60, max: 60).

    Returns:
        Dict with success, status, and ticket_id (E2B) or execution result (legacy).
    """
    if not code or not code.strip():
        return {
            "success": False,
            "error": "No code provided.",
        }

    settings = get_settings()

    # E2B sandbox path
    if settings.ENABLE_SANDBOX and settings.E2B_API_KEY:
        return await _execute_e2b(ctx, code, input_files)

    # Legacy fallback: in-process sandbox
    return await _execute_legacy(ctx, code, min(timeout, 30))


async def _execute_e2b(
    ctx: SkillContext,
    code: str,
    input_files: list[str] | None,
) -> Dict[str, Any]:
    """Create a review ticket for E2B sandbox execution."""
    if not ctx.session_id:
        return {
            "success": False,
            "error": "No active session. Code execution requires a session.",
        }

    try:
        from app.services.review_queue import ReviewQueueService, ReviewTicketCreate

        if not ctx.db:
            return {
                "success": False,
                "error": "Database session required for code execution review.",
            }

        review_queue = ReviewQueueService(db=ctx.db)

        data = ReviewTicketCreate(
            title=f"Code execution review: {code[:80]}...",
            description="Agent requests Python execution in E2B sandbox",
            risk_level="high",
            review_type="code_execution",
            review_data={
                "code": code,
                "input_files": input_files or [],
            },
            session_id=ctx.session_id,
            agent_id=str(ctx.metadata.get("agent_id", "unknown")),
        )

        ticket = await review_queue.create_ticket(
            tenant_id=str(ctx.tenant_id),
            data=data,
            requested_by=str(ctx.user_id),
        )

        logger.info(
            f"Created code execution review ticket: {ticket.id} "
            f"for session {ctx.session_id}"
        )

        return {
            "success": True,
            "status": "pending_review",
            "ticket_id": str(ticket.id),
            "message": (
                "Code submitted for human review. "
                "Execution will proceed after approval."
            ),
        }

    except Exception as e:
        logger.error(f"Failed to create review ticket: {e}")
        return {
            "success": False,
            "error": f"Failed to submit code for review: {e}",
        }


async def _execute_legacy(
    ctx: SkillContext,
    code: str,
    timeout: int,
) -> Dict[str, Any]:
    """Legacy in-process sandbox execution (backward compatibility)."""
    from app.skills.execute_python.tool_legacy import execute_python as legacy_execute
    return await legacy_execute(ctx, code, timeout)
