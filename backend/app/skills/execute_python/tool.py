"""
Execute Python Skill (E2B Sandbox)

Executes Python code in an E2B Firecracker microVM with hypervisor-level isolation.
Runs only through the tool gateway, after a human approved this exact call (the engine waits for
the approval; `execute_tool` binds the approved arguments and single use).

When ENABLE_SANDBOX is False the skill refuses. There is no in-process fallback.

Timeout: 60 seconds (E2B remote).
"""

import logging
from typing import Any, Dict

from app.agents.skills.context import SkillContext
from app.config import get_settings
from app.internal_api.execution import ToolOutcome

logger = logging.getLogger(__name__)


async def execute_python(
    ctx: SkillContext,
    code: str,
    input_files: list[str] | None = None,
    timeout: int = 60,
) -> Dict[str, Any] | ToolOutcome:
    """
    Execute Python code in an E2B Firecracker sandbox.

    When ENABLE_SANDBOX is True and E2B_API_KEY is configured and the call came through the
    gateway (already approved), runs the code in the sandbox and returns its output. Outside the
    gateway it refuses and creates nothing.

    When ENABLE_SANDBOX is False, or the sandbox cannot start, returns an error ToolOutcome so the
    approval is not consumed (never executes in-process). Any run that executed consumes it.

    Args:
        ctx: Skill execution context (must have session_id).
        code: Python code to execute.
        input_files: Optional workspace files to upload to sandbox.
        timeout: Maximum execution time in seconds (default: 60, max: 60).

    Returns:
        Dict with success, stdout, stderr, error and output_files.
    """
    if not code or not code.strip():
        return {
            "success": False,
            "error": "No code provided.",
        }

    settings = get_settings()

    # E2B sandbox path
    if settings.ENABLE_SANDBOX and settings.E2B_API_KEY:
        return await _execute_e2b(ctx, code, input_files, timeout)

    # Fail closed: no in-process fallback
    from app.agents.skills.code import SANDBOX_REFUSAL

    return _not_run(SANDBOX_REFUSAL)


def _not_run(message: str) -> ToolOutcome:
    """The code did not execute: a tool error, so the gateway leaves the approval unconsumed (retryable)."""
    return ToolOutcome(content=message, is_error=True, details={"error": "sandbox_unavailable"})


NEEDS_APPROVAL_REFUSAL = "Code execution needs an approved request. Use engine chat."
MAX_TIMEOUT_S = 60
MAX_INPUT_FILES = 20


def _executor():
    """Seam for tests: the sandbox executor."""
    from app.services.sandbox_executor import SandboxExecutor

    return SandboxExecutor()


async def _execute_e2b(
    ctx: SkillContext,
    code: str,
    input_files: list[str] | None,
    timeout: int = MAX_TIMEOUT_S,
) -> Dict[str, Any]:
    """Run approved code in the sandbox.

    Only the tool gateway may reach this: it has already waited for a human decision and
    ``execute_tool`` has bound the call to the approved arguments. Anywhere else (legacy Python
    agent loops) the skill refuses and creates nothing.
    """
    run_id = ctx.metadata.get("run_id")
    if not ctx.metadata.get("tool_call_id") or not run_id:
        return {"success": False, "error": NEEDS_APPROVAL_REFUSAL}

    timeout = max(1, min(int(timeout or MAX_TIMEOUT_S), MAX_TIMEOUT_S))
    executor = _executor()
    worker_id = f"exec-{ctx.metadata['tool_call_id']}"[:64]
    started = False  # becomes True once the code is handed to the sandbox
    try:
        handle = await executor.get_or_create(str(run_id), worker_id)
        try:
            uploads = await _read_input_files(ctx, input_files)
            if uploads:
                await handle.upload_files(uploads)
            started = True
            result = await handle.execute_code(code, timeout=timeout)
        finally:
            try:
                await executor.teardown(str(run_id), worker_id)
            except Exception:
                logger.warning("Sandbox teardown failed for run %s", run_id, exc_info=True)
    except Exception as e:
        logger.warning("execute_python sandbox failure: %s", type(e).__name__)
        message = f"Sandbox execution failed: {e}"
        if started:  # the code may have run: report it, and the approval stays spent
            return {"success": False, "error": message}
        return _not_run(message)

    return {
        "success": bool(result.success),
        "status": "completed" if result.success else "failed",
        "stdout": result.stdout,
        "stderr": result.stderr,
        "error": result.error,
        "output_files": result.output_files or [],
        "duration_seconds": result.duration_seconds,
    }


async def _read_input_files(ctx: SkillContext, input_files: list[str] | None) -> dict[str, bytes | str]:
    """Workspace files named by the call, read for upload (empty when there are none)."""
    if not input_files:
        return {}
    from app.services.session_storage import get_session_storage

    storage = get_session_storage()
    out: dict[str, bytes | str] = {}
    for path in input_files[:MAX_INPUT_FILES]:
        if ".." in path.split("/") or path.startswith("/"):
            continue
        content = await storage.read(str(ctx.tenant_id), str(ctx.session_id), f"workspace/{path}")
        if content is not None:
            out[path] = content
    return out
