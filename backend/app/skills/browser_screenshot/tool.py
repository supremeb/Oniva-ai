"""
Browser Screenshot Skill

Take a screenshot of a URL using headless Chromium inside the E2B sandbox.
Saves the PNG to the session workspace.

Requires: ENABLE_SANDBOX=True, ENABLE_BROWSER_SANDBOX=True, E2B_API_KEY set.
"""

import json
import logging
from typing import Any, Dict

from app.agents.skills.context import SkillContext
from app.config import get_settings
from app.security.url_validator import is_url_safe

logger = logging.getLogger(__name__)

# Playwright script executed inside the E2B sandbox.
# Uses Playwright directly (no LLM needed for screenshots).
_SCREENSHOT_SCRIPT = '''
import asyncio, json, os

async def main():
    from playwright.async_api import async_playwright

    url = {url!r}
    full_page = {full_page!r}
    filename = {filename!r}
    output_path = f"/workspace/{{filename}}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu"],
        )
        page = await browser.new_page(viewport={{"width": 1280, "height": 720}})
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.screenshot(path=output_path, full_page=full_page)
        await browser.close()

    size = os.path.getsize(output_path)
    print(json.dumps({{
        "status": "success",
        "url": url,
        "file_path": output_path,
        "size_bytes": size,
    }}))

await main()
'''


async def browser_screenshot(
    ctx: SkillContext,
    url: str,
    full_page: bool = True,
    filename: str = "screenshot.png",
) -> Dict[str, Any]:
    """
    Take a screenshot of a URL in the sandbox browser.

    Args:
        ctx: Skill execution context.
        url: URL to screenshot.
        full_page: Whether to capture the full scrollable page.
        filename: Output filename (saved under /workspace/).

    Returns:
        Dict with url, status, file_path, and size_bytes.
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

    # Sanitize filename
    if "/" in filename or ".." in filename:
        return {"url": url, "error": "Invalid filename", "status": "blocked"}

    # Script passed directly to E2B code interpreter (Jupyter kernel)
    script = _SCREENSHOT_SCRIPT.format(
        url=url, full_page=full_page, filename=filename,
    )

    try:
        from app.services.sandbox_executor import get_sandbox_executor

        executor = get_sandbox_executor()

        # execute_with_lifecycle passes code to run_code() (Jupyter kernel)
        # timeout=120 for Chromium launch + page load + screenshot.
        result = await executor.execute_with_lifecycle(
            code=script,
            tenant_id=str(ctx.tenant_id),
            session_id=ctx.session_id or "browser_ss",
            timeout=120,
        )

        if not result.success:
            return {
                "url": url,
                "error": result.error or result.stderr,
                "status": "failed",
            }

        try:
            output = json.loads(result.stdout.strip().split("\n")[-1])
            return output
        except (json.JSONDecodeError, IndexError):
            return {
                "url": url,
                "status": "success",
                "content": result.stdout[:5000],
            }

    except Exception as e:
        logger.error("browser_screenshot failed: %s", e)
        return {
            "url": url,
            "error": str(e),
            "status": "failed",
        }
