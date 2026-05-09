"""
Complete Task Skill
Signal that the autonomous execution is complete with a summary.
"""

from typing import Any, Dict, List, Optional

from app.agents.skills.context import SkillContext
from app.agents.skills.registry import skill


@skill(
    name="complete_task",
    description="Signal that the current task is complete. "
    "Provide a summary of what was accomplished and key findings.",
    category="planning",
    version="1.0.0",
    author="oniva",
)
async def complete_task(
    ctx: SkillContext,
    summary: str,
    key_findings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Signal completion of the autonomous execution.

    The AutonomousExecutor detects this result and exits the loop.
    The summary and key findings are saved as an artifact and streamed
    as a deep_work_complete SSE event.

    Args:
        ctx: Skill execution context
        summary: A summary of what was accomplished
        key_findings: Key findings or results (optional)

    Returns:
        Dict signaling the executor to finalize
    """
    if not summary.strip():
        return {
            "success": False,
            "error": "Summary cannot be empty.",
        }

    key_findings = key_findings or []

    # Save summary as artifact if session is active
    if ctx.session_id:
        try:
            from app.services.session_storage import get_session_storage

            storage = get_session_storage()

            # Build report content
            report_lines = [f"# Task Summary\n", summary.strip(), ""]
            if key_findings:
                report_lines.append("## Key Findings\n")
                for finding in key_findings:
                    report_lines.append(f"- {finding}")

            report_content = "\n".join(report_lines)
            await storage.write(
                str(ctx.tenant_id),
                ctx.session_id,
                "workspace/task_summary.md",
                report_content,
            )
        except Exception:
            pass  # Non-critical: summary still returned in event

    return {
        "success": True,
        "type": "complete",
        "summary": summary.strip(),
        "key_findings": [f.strip() for f in key_findings if f.strip()],
    }
