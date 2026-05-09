"""Validate the oniva-agent E2B sandbox template has all required packages."""

import asyncio
import os
import sys

from e2b import AsyncSandbox
from e2b.sandbox.commands.command_handle import CommandExitException


async def run_cmd(sandbox, cmd, timeout=30):
    """Run a command, returning (stdout, stderr, exit_code) without raising."""
    try:
        r = await sandbox.commands.run(cmd, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip() if r.stderr else "", 0
    except CommandExitException as e:
        return "", str(e)[:300], 1


async def main():
    api_key = os.environ.get("E2B_API_KEY")
    if not api_key:
        print("ERROR: E2B_API_KEY not set")
        sys.exit(1)

    print("Creating sandbox from oniva-agent template...")
    sandbox = await AsyncSandbox.create("oniva-agent", timeout=120, api_key=api_key)
    print(f"Sandbox created: {sandbox.sandbox_id}")

    passed = 0
    failed = 0

    # Test 1: Playwright import
    print("\n--- Test 1: Playwright import ---")
    out, err, code = await run_cmd(sandbox, 'python3 -c "from playwright.async_api import async_playwright; print(\'playwright OK\')"')
    print(f"  {out or err}")
    if "OK" in out:
        passed += 1
    else:
        failed += 1

    # Test 2: Browser Use import
    print("\n--- Test 2: Browser Use import ---")
    out, err, code = await run_cmd(sandbox, 'python3 -c "from browser_use import Agent; print(\'browser_use OK\')"')
    print(f"  {out or err}")
    if "OK" in out:
        passed += 1
    else:
        failed += 1

    # Test 3: Playwright Chromium binary
    print("\n--- Test 3: Playwright Chromium ---")
    out, err, code = await run_cmd(
        sandbox,
        "PLAYWRIGHT_BROWSERS_PATH=/ms-playwright python3 -c \"from playwright._impl._driver import compute_driver_executable; print('Playwright driver OK')\"",
    )
    print(f"  {out or err}")
    if "OK" in out:
        passed += 1
    else:
        failed += 1

    # Test 4: Playwright screenshot of example.com
    print("\n--- Test 4: Playwright screenshot ---")
    await sandbox.files.write("/workspace/test_pw.py", """
import asyncio, os
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        await page.goto("https://example.com", wait_until="networkidle", timeout=30000)
        title = await page.title()
        await page.screenshot(path="/workspace/test_screenshot.png")
        await browser.close()
        print(f"Title: {title}")
        print("Screenshot saved OK")

asyncio.run(main())
""")
    out, err, code = await run_cmd(sandbox, "python3 /workspace/test_pw.py", timeout=60)
    print(f"  {out}")
    if err:
        print(f"  stderr: {err[-200:]}")
    if "OK" in out:
        passed += 1
    else:
        failed += 1

    # Test 5: Verify screenshot file
    print("\n--- Test 5: Screenshot file ---")
    out, err, code = await run_cmd(sandbox, "ls -la /workspace/test_screenshot.png 2>/dev/null && echo FILE_EXISTS || echo MISSING")
    print(f"  {out}")
    if "FILE_EXISTS" in out:
        passed += 1
    else:
        failed += 1

    # Test 6: Data science libs
    print("\n--- Test 6: Data science libs ---")
    out, err, code = await run_cmd(
        sandbox,
        "python3 -c \"import pandas, numpy, matplotlib, seaborn, scipy, sklearn; print('All data science libs OK')\"",
    )
    print(f"  {out or err}")
    if "OK" in out:
        passed += 1
    else:
        failed += 1

    # Test 7: yfinance
    print("\n--- Test 7: yfinance ---")
    out, err, code = await run_cmd(sandbox, 'python3 -c "import yfinance; print(\'yfinance OK\')"')
    print(f"  {out or err}")
    if "OK" in out:
        passed += 1
    else:
        failed += 1

    # Test 8: Web scraping libs
    print("\n--- Test 8: Web scraping libs ---")
    out, err, code = await run_cmd(sandbox, 'python3 -c "import requests, bs4, httpx; print(\'web scraping libs OK\')"')
    print(f"  {out or err}")
    if "OK" in out:
        passed += 1
    else:
        failed += 1

    await sandbox.kill()

    print(f"\n{'=' * 40}")
    print(f"Results: {passed} passed, {failed} failed out of {passed + failed}")
    if failed == 0:
        print("All validation tests passed!")
    else:
        print("Some tests FAILED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
