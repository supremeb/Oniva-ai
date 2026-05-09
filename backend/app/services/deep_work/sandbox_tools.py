"""
SandboxToolRouter — centralizes sandbox tool schemas and routing for deep work.

Provides OpenAI function-calling schemas for sandbox-only tools (shell_exec,
browser_navigate, browser_screenshot) and routes tool calls to the sandbox.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

from app.config import get_settings
from app.services.deep_work.event_sink import EventSink

if TYPE_CHECKING:
    from app.services.sandbox_executor import SandboxHandle

logger = logging.getLogger(__name__)


# ── Tool Schemas (OpenAI function-calling format) ──────────────

SHELL_EXEC_TOOL = {
    "type": "function",
    "function": {
        "name": "shell_exec",
        "description": (
            "Execute a shell command in the sandbox environment. "
            "Use for running Python scripts, file operations, and data analysis. "
            "The sandbox has pandas, numpy, matplotlib, seaborn, scipy, "
            "statsmodels, scikit-learn, yfinance, requests, and beautifulsoup4."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute (e.g., 'python3 /workspace/analyze.py')",
                }
            },
            "required": ["command"],
        },
    },
}

BROWSER_NAV_TOOL = {
    "type": "function",
    "function": {
        "name": "browser_navigate",
        "description": (
            "Navigate to a URL using a headless Chromium browser in the sandbox. "
            "Renders JavaScript and extracts page content. Use for SPAs, "
            "dynamic pages, or sites that block simple HTTP requests."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to navigate to (must be http/https)",
                },
                "task": {
                    "type": "string",
                    "description": "What to extract from the page (default: main text content)",
                },
            },
            "required": ["url"],
        },
    },
}

BROWSER_SCREENSHOT_TOOL = {
    "type": "function",
    "function": {
        "name": "browser_screenshot",
        "description": (
            "Take a screenshot of a URL using headless Chromium in the sandbox. "
            "Saves a PNG file to /workspace/. Use for visual verification "
            "or capturing dynamic page state."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to screenshot (must be http/https)",
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Capture full scrollable page (default: true)",
                },
                "filename": {
                    "type": "string",
                    "description": "Output filename (default: screenshot.png)",
                },
            },
            "required": ["url"],
        },
    },
}

INSPECT_ARTIFACT_TOOL = {
    "type": "function",
    "function": {
        "name": "inspect_artifact",
        "description": (
            "Inspect a generated file in the sandbox workspace. "
            "Returns metadata (size, page count) and screenshots for visual verification. "
            "Use this after generating a deliverable to check quality before publishing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path in the sandbox workspace (e.g., '/workspace/report.pdf')",
                },
            },
            "required": ["path"],
        },
    },
}

PUBLISH_ARTIFACT_TOOL = {
    "type": "function",
    "function": {
        "name": "publish_artifact",
        "description": (
            "Publish a completed deliverable from the sandbox to persistent storage. "
            "Downloads the file while the sandbox is alive, uploads to artifact storage, "
            "and emits a rich file card to the user. Call this only after inspect_artifact "
            "confirms the file is acceptable."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path in the sandbox workspace (e.g., '/workspace/report.pdf')",
                },
                "title": {
                    "type": "string",
                    "description": "Human-readable title for the deliverable",
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of the deliverable content",
                },
                "artifact_type": {
                    "type": "string",
                    "enum": ["pdf_report", "presentation", "spreadsheet", "dashboard", "chart", "document", "data_export"],
                    "description": "Type of deliverable",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for categorization",
                },
            },
            "required": ["path", "title", "artifact_type"],
        },
    },
}

# ── Sandbox tool names for routing ─────────────────────────────

SANDBOX_TOOL_NAMES = frozenset({
    "shell_exec", "browser_navigate", "browser_screenshot",
    "inspect_artifact", "publish_artifact",
})


def get_sandbox_tools(
    include_browser: bool = False,
    include_output: bool = False,
) -> list[dict]:
    """Return sandbox tool schemas based on feature flags.

    Args:
        include_browser: Override to force browser tools. If False, checks
            the ENABLE_BROWSER_SANDBOX setting.
        include_output: If True, include inspect_artifact and publish_artifact
            tools for the output generation phase.

    Returns:
        List of OpenAI function-calling tool schemas.
    """
    tools = [SHELL_EXEC_TOOL]

    if include_browser or getattr(get_settings(), "ENABLE_BROWSER_SANDBOX", False):
        tools.append(BROWSER_NAV_TOOL)
        tools.append(BROWSER_SCREENSHOT_TOOL)

    if include_output:
        tools.append(INSPECT_ARTIFACT_TOOL)
        tools.append(PUBLISH_ARTIFACT_TOOL)

    return tools


async def execute_sandbox_tool(
    tool_name: str,
    arguments: dict[str, Any],
    sandbox: SandboxHandle,
    api_keys: dict[str, str] | None = None,
    event_sink: EventSink | None = None,
    artifact_ctx: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Route a sandbox tool call to the appropriate handler.

    Args:
        tool_name: Name of the tool to execute.
        arguments: Tool arguments from the LLM.
        sandbox: Active sandbox handle.
        api_keys: Optional API keys for browser tools (e.g., OpenAI for Browser Use).
        event_sink: Optional EventSink for emitting events (needed by publish_artifact).
        artifact_ctx: Optional context dict with tenant_id, session_id, user_id
            (needed by publish_artifact to store files).

    Returns:
        Dict with result or error.
    """
    if tool_name == "shell_exec":
        return await _exec_shell(sandbox, arguments)
    elif tool_name == "browser_navigate":
        return await _exec_browser_navigate(sandbox, arguments, api_keys)
    elif tool_name == "browser_screenshot":
        return await _exec_browser_screenshot(sandbox, arguments)
    elif tool_name == "inspect_artifact":
        return await _exec_inspect_artifact(sandbox, arguments)
    elif tool_name == "publish_artifact":
        return await _exec_publish_artifact(sandbox, arguments, event_sink, artifact_ctx)
    else:
        return {"error": f"Unknown sandbox tool: {tool_name}"}


async def _exec_shell(
    sandbox: SandboxHandle, arguments: dict[str, Any],
) -> dict[str, Any]:
    """Execute a shell command in the sandbox."""
    command = arguments.get("command", "")
    try:
        result = await sandbox.run_shell(command)
        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR: {result.stderr}"
        if result.exit_code != 0:
            output += f"\nExit code: {result.exit_code}"
        return {"result": output}
    except Exception as e:
        logger.warning("shell_exec failed: %s", e)
        return {"error": str(e)}


async def _exec_browser_navigate(
    sandbox: SandboxHandle,
    arguments: dict[str, Any],
    api_keys: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Navigate to a URL in the sandbox browser."""
    from app.security.url_validator import is_url_safe

    url = arguments.get("url", "")
    task = arguments.get("task", "extract the main text content of the page")

    error = is_url_safe(url)
    if error:
        return {"error": error, "status": "blocked"}

    # Browser Use script that runs inside the sandbox via execute_code()
    script = (
        'import asyncio, json, os\n'
        'os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")\n'
        'async def main():\n'
        '    from browser_use import Agent\n'
        '    from langchain_openai import ChatOpenAI\n'
        '    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)\n'
        f'    agent = Agent(task="Go to {url} and {task}", llm=llm)\n'
        '    result = await agent.run()\n'
        f'    print(json.dumps({{"status": "success", "url": {url!r}, "content": str(result)}}))\n'
        'await main()\n'
    )
    try:
        # LLM API keys are injected at sandbox creation time via env_vars
        # (see engine.py _build_sandbox_env_vars), so they're already
        # available to the python process.
        result = await sandbox.execute_code(script, timeout=120)
        if not result.success:
            return {"error": result.stderr or result.error or "Navigation failed", "status": "failed"}

        try:
            return json.loads(result.stdout.strip().split("\n")[-1])
        except (json.JSONDecodeError, IndexError):
            return {"status": "success", "url": url, "content": result.stdout[:10000]}
    except Exception as e:
        logger.warning("browser_navigate failed: %s", e)
        return {"error": str(e), "status": "failed"}


async def _exec_browser_screenshot(
    sandbox: SandboxHandle, arguments: dict[str, Any],
) -> dict[str, Any]:
    """Take a screenshot in the sandbox browser."""
    from app.security.url_validator import is_url_safe

    url = arguments.get("url", "")
    full_page = arguments.get("full_page", True)
    filename = arguments.get("filename", "screenshot.png")

    error = is_url_safe(url)
    if error:
        return {"error": error, "status": "blocked"}

    if "/" in filename or ".." in filename:
        return {"error": "Invalid filename", "status": "blocked"}

    script = (
        'import asyncio, json, os\n'
        'os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")\n'
        'async def main():\n'
        '    from playwright.async_api import async_playwright\n'
        '    async with async_playwright() as p:\n'
        '        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])\n'
        '        page = await browser.new_page(viewport={"width": 1280, "height": 720})\n'
        f'        await page.goto({url!r}, wait_until="networkidle", timeout=30000)\n'
        f'        await page.screenshot(path="/workspace/{filename}", full_page={full_page})\n'
        '        await browser.close()\n'
        f'    size = os.path.getsize("/workspace/{filename}")\n'
        f'    print(json.dumps({{"status": "success", "url": {url!r}, "file_path": "/workspace/{filename}", "size_bytes": size}}))\n'
        'await main()\n'
    )
    try:
        result = await sandbox.execute_code(script, timeout=120)
        if not result.success:
            return {"error": result.stderr or result.error or "Screenshot failed", "status": "failed"}

        try:
            return json.loads(result.stdout.strip().split("\n")[-1])
        except (json.JSONDecodeError, IndexError):
            return {"status": "success", "url": url, "content": result.stdout[:5000]}
    except Exception as e:
        logger.warning("browser_screenshot failed: %s", e)
        return {"error": str(e), "status": "failed"}


# ── Output phase tools ─────────────────────────────────────────


async def _exec_inspect_artifact(
    sandbox: SandboxHandle, arguments: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a generated file in the sandbox workspace."""
    path = arguments.get("path", "")
    if not path or ".." in path:
        return {"error": "Invalid path"}

    # Run inspection script in sandbox
    script = (
        'import json, os\n'
        'path = ' + repr(path) + '\n'
        'result = {"path": path, "exists": False}\n'
        'if os.path.exists(path):\n'
        '    result["exists"] = True\n'
        '    result["size_bytes"] = os.path.getsize(path)\n'
        '    result["size"] = f"{result[\"size_bytes\"] / 1024:.1f} KB"\n'
        '    ext = os.path.splitext(path)[1].lower()\n'
        '    result["extension"] = ext\n'
        '    issues = []\n'
        '    pages = None\n'
        '    if ext == ".pdf":\n'
        '        try:\n'
        '            import fitz\n'
        '            doc = fitz.open(path)\n'
        '            pages = doc.page_count\n'
        '            if pages == 0:\n'
        '                issues.append("PDF has 0 pages")\n'
        '            doc.close()\n'
        '        except Exception as e:\n'
        '            issues.append(f"PDF read error: {e}")\n'
        '    elif ext == ".pptx":\n'
        '        try:\n'
        '            from pptx import Presentation\n'
        '            prs = Presentation(path)\n'
        '            pages = len(prs.slides)\n'
        '            if pages == 0:\n'
        '                issues.append("Presentation has 0 slides")\n'
        '        except Exception as e:\n'
        '            issues.append(f"PPTX read error: {e}")\n'
        '    elif ext == ".xlsx":\n'
        '        try:\n'
        '            import openpyxl\n'
        '            wb = openpyxl.load_workbook(path, read_only=True)\n'
        '            pages = len(wb.sheetnames)\n'
        '            total_rows = sum(ws.max_row or 0 for ws in wb.worksheets)\n'
        '            result["total_rows"] = total_rows\n'
        '            result["sheets"] = wb.sheetnames\n'
        '            wb.close()\n'
        '        except Exception as e:\n'
        '            issues.append(f"XLSX read error: {e}")\n'
        '    elif ext == ".docx":\n'
        '        try:\n'
        '            from docx import Document\n'
        '            doc = Document(path)\n'
        '            pages = len(doc.paragraphs)\n'
        '            result["paragraph_count"] = pages\n'
        '        except Exception as e:\n'
        '            issues.append(f"DOCX read error: {e}")\n'
        '    elif ext in (".html", ".htm"):\n'
        '        try:\n'
        '            with open(path) as f:\n'
        '                content = f.read()\n'
        '            result["char_count"] = len(content)\n'
        '            if len(content) < 100:\n'
        '                issues.append("HTML file seems too small")\n'
        '        except Exception as e:\n'
        '            issues.append(f"HTML read error: {e}")\n'
        '    if result["size_bytes"] == 0:\n'
        '        issues.append("File is empty (0 bytes)")\n'
        '    if pages is not None:\n'
        '        result["pages"] = pages\n'
        '    result["issues"] = issues\n'
        '    result["passed"] = len(issues) == 0\n'
        'else:\n'
        '    result["issues"] = [f"File not found: {path}"]\n'
        '    result["passed"] = False\n'
        'print(json.dumps(result))\n'
    )

    try:
        result = await sandbox.execute_code(script, timeout=30)
        if not result.success:
            return {"error": result.stderr or result.error or "Inspection failed"}

        try:
            return json.loads(result.stdout.strip().split("\n")[-1])
        except (json.JSONDecodeError, IndexError):
            return {"error": "Failed to parse inspection result", "stdout": result.stdout[:2000]}
    except Exception as e:
        logger.warning("inspect_artifact failed: %s", e)
        return {"error": str(e)}


async def _exec_publish_artifact(
    sandbox: SandboxHandle,
    arguments: dict[str, Any],
    event_sink: EventSink | None = None,
    artifact_ctx: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Publish an artifact from sandbox to persistent storage."""
    from app.services.artifacts import ArtifactStorage

    path = arguments.get("path", "")
    title = arguments.get("title", "Deliverable")
    description = arguments.get("description", "")
    artifact_type = arguments.get("artifact_type", "document")
    tags = arguments.get("tags", [])

    if not path or ".." in path:
        return {"error": "Invalid path"}
    if not artifact_ctx:
        return {"error": "Missing artifact context (tenant_id, session_id)"}

    tenant_id = artifact_ctx["tenant_id"]
    session_id = artifact_ctx["session_id"]

    try:
        # Download file from sandbox while it's still alive
        # Use sandbox's internal _sandbox.files.read() for arbitrary paths
        # Binary files (PDF, PPTX, etc.) must use format='bytes' to avoid corruption
        BINARY_EXTENSIONS = {'.pdf', '.pptx', '.xlsx', '.docx', '.png', '.jpg', '.jpeg', '.gif', '.zip', '.tar', '.gz', '.bmp', '.webp', '.svg', '.ico'}
        ext = os.path.splitext(path)[1].lower()
        read_format = 'bytes' if ext in BINARY_EXTENSIONS else 'text'
        raw = await sandbox._sandbox.files.read(path, format=read_format)
        file_content = raw if isinstance(raw, bytes) else raw.encode("utf-8")
        if not file_content:
            return {"error": f"Failed to download file from sandbox: {path}"}

        # Determine filename
        filename = path.rsplit("/", 1)[-1] if "/" in path else path

        # Upload to artifact storage
        storage = ArtifactStorage()
        artifact_uri = await storage.write(
            tenant_id=tenant_id,
            session_id=session_id,
            file_path=f"deliverables/{filename}",
            content=file_content,
            metadata={
                "title": title,
                "description": description,
                "artifact_type": artifact_type,
                "tags": ",".join(tags) if tags else "",
                "user_id": artifact_ctx.get("user_id", ""),
            },
        )

        # Generate download URL
        download_url = await storage.get_download_url(
            tenant_id=tenant_id,
            session_id=session_id,
            file_path=f"deliverables/{filename}",
        )

        # Emit enriched FilePart SSE event
        if event_sink:
            await event_sink.emit("deep_work_engine_output_complete", {
                "artifact_uri": artifact_uri,
                "download_url": download_url,
                "preview_url": None,
                "file_part": {
                    "type": "file",
                    "filename": filename,
                    "size": len(file_content),
                    "fileType": _guess_mime_type(filename),
                    "downloadUrl": download_url,
                    "title": title,
                    "description": description,
                    "artifactType": artifact_type,
                    "artifactUri": artifact_uri,
                    "tags": tags,
                    "pageCount": None,
                },
            })

        return {
            "status": "published",
            "artifact_uri": artifact_uri,
            "download_url": download_url,
            "filename": filename,
            "size_bytes": len(file_content),
        }
    except Exception as e:
        logger.exception("publish_artifact failed: %s", e)
        return {"error": str(e)}


def _guess_mime_type(filename: str) -> str:
    """Guess MIME type from filename extension."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "pdf": "application/pdf",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "html": "text/html",
        "htm": "text/html",
        "csv": "text/csv",
        "png": "image/png",
        "json": "application/json",
    }.get(ext, "application/octet-stream")
