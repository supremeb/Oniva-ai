"""
Browser Navigate Skill

Navigate to a URL using a full Chromium browser inside E2B sandbox.
Requires ENABLE_BROWSER_SANDBOX=True. For simple static pages, browse_url is faster and free.

Requires: ENABLE_SANDBOX=True, ENABLE_BROWSER_SANDBOX=True, E2B_API_KEY set.
"""

import json
import logging
from typing import Any, Dict

from app.agents.skills.context import SkillContext
from app.config import get_settings
from app.security.url_validator import is_url_safe

logger = logging.getLogger(__name__)

# Python script template executed inside the E2B sandbox.
# Browser Use handles launching Chromium and driving the page.
_BROWSER_SCRIPT = '''
import asyncio, json, sys

async def main():
    from browser_use import Agent
    from langchain_openai import ChatOpenAI

    url = {url!r}
    task = {task!r}

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    agent = Agent(
        task=f"Go to {{url}} and {{task}}",
        llm=llm,
    )
    result = await agent.run()

    print(json.dumps({{
        "status": "success",
        "url": url,
        "content": str(result),
    }}))

await main()
'''


async def browser_navigate(
    ctx: SkillContext,
    url: str,
    task: str = "extract the main text content of the page",
) -> Dict[str, Any]:
    """
    Navigate to a URL in the sandbox browser and extract content.

    Args:
        ctx: Skill execution context.
        url: URL to navigate to.
        task: Instruction for what to extract from the page.

    Returns:
        Dict with url, status, and extracted content.
    """
    settings = get_settings()

    if not settings.ENABLE_SANDBOX or not settings.ENABLE_BROWSER_SANDBOX:
        return {
            "url": url,
            "error": "Browser sandbox is not enabled. Set ENABLE_SANDBOX=True and ENABLE_BROWSER_SANDBOX=True.",
            "status": "disabled",
        }

    if not settings.E2B_API_KEY:
        return {
            "url": url,
            "error": "E2B API key not configured.",
            "status": "disabled",
        }

    # SSRF protection
    error = is_url_safe(url)
    if error:
        return {"url": url, "error": error, "status": "blocked"}

    # Build the script — passed directly to E2B code interpreter (Jupyter kernel)
    script = _BROWSER_SCRIPT.format(url=url, task=task)

    try:
        from app.services.sandbox_executor import get_sandbox_executor

        executor = get_sandbox_executor()

        # Zero-secrets: use proxy token instead of real key when control plane enabled
        sandbox_token = None
        if settings.ENABLE_SANDBOX_CONTROL_PLANE and settings.SANDBOX_PROXY_URL:
            from app.services.sandbox_tokens import create_sandbox_token, revoke_sandbox_token

            sandbox_token = await create_sandbox_token(
                tenant_id=str(ctx.tenant_id),
                user_id=str(ctx.user_id),
                session_id=ctx.session_id or "browser_nav",
                worker_id="browser_navigate",
            )
            env_vars = {
                "OPENAI_API_KEY": sandbox_token,
                "OPENAI_BASE_URL": f"{settings.SANDBOX_PROXY_URL}/api/v1/sandbox-proxy/v1",
            }
        else:
            env_vars = {"OPENAI_API_KEY": ctx.api_keys.get("openai", "")}

        # execute_with_lifecycle passes code to run_code() (Jupyter kernel)
        # Browser Use needs OPENAI_API_KEY for the LLM; timeout=120 for Chromium+LLM chain.
        try:
            result = await executor.execute_with_lifecycle(
                code=script,
                tenant_id=str(ctx.tenant_id),
                session_id=ctx.session_id or "browser_nav",
                timeout=120,
                env_vars=env_vars,
            )
        finally:
            # Revoke control plane token after sandbox lifecycle completes
            if sandbox_token:
                try:
                    await revoke_sandbox_token(sandbox_token)
                except Exception:
                    logger.warning("Failed to revoke browser_navigate sandbox token")

        if not result.success:
            return {
                "url": url,
                "error": result.error or result.stderr,
                "status": "failed",
            }

        # Parse the JSON output from the script
        try:
            output = json.loads(result.stdout.strip().split("\n")[-1])
            return output
        except (json.JSONDecodeError, IndexError):
            return {
                "url": url,
                "status": "success",
                "content": result.stdout[:10000],
            }

    except Exception as e:
        logger.error("browser_navigate failed: %s", e)
        return {
            "url": url,
            "error": str(e),
            "status": "failed",
        }
