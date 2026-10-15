"""Deep Work sandbox and output gateway tools for the engine path (deep-work-on-engine plan, U5, KTD10).

Workers and the output agent reach a sandbox only through these tools. The security model is the
legacy one: a sandbox gets a control-plane proxy token (never a provider key), fails closed without
``SANDBOX_PROXY_URL``, and is created through ``SandboxExecutor`` (egress allow-list, cost cap).

Sandbox identity is ``(deep_work_runs.id, worker_id)``. The model passes ``worker_id`` as an argument;
it is validated against the registered rounds, and is used only to name a sandbox inside a run the
gateway already authorized. The output agent's sandbox is ``output_agent``.

Proxy tokens are indexed in a Redis hash per run (``worker_id -> token``) so any backend pod can
revoke them: cancel, the lazy reaper and the teardown hooks in ``dw_save_learnings`` / ``dw_finalize``
all go through ``teardown_worker`` / ``teardown_all``. The executor's TTL is the backstop.
"""
import json
import logging
from typing import Any, Dict, Iterable, List, Optional

from app.agents.skills.context import SkillContext
from app.agents.skills.registry import skill
from app.config import get_settings
from app.internal_api.execution import ToolOutcome
from app.services import deep_work_state as dws
from app.services.deep_work import engine_tools as et
from app.services.deep_work.sandbox_tools import _guess_mime_type, execute_sandbox_tool
from app.services.redis_ha import get_redis_service
from app.services.sandbox_executor import get_sandbox_executor

logger = logging.getLogger(__name__)

OUTPUT_WORKER = "output_agent"
# Tool names served to the engine. The two browser tools carry a ``dw_`` prefix because the chat
# skills ``browser_navigate`` / ``browser_screenshot`` already own those names in the registry.
SANDBOX_TOOL_NAMES = (
    "shell_exec", "dw_browser_navigate", "dw_browser_screenshot", "inspect_artifact", "publish_artifact",
)
BROWSER_TOOL_NAMES = ("dw_browser_navigate", "dw_browser_screenshot")
_LEGACY_NAME = {"dw_browser_navigate": "browser_navigate", "dw_browser_screenshot": "browser_screenshot"}
MAX_OUTPUT_CHARS = 20_000
_INDEX_TTL = 7200


# --- availability ---------------------------------------------------------------


def sandbox_unavailable_reason(api_key: Optional[str] = None) -> Optional[str]:
    """Why sandbox tools cannot run, or ``None``. U6 calls this when building the execute config."""
    settings = get_settings()
    if not settings.ENABLE_SANDBOX:
        return "Sandbox execution is disabled."
    if not (api_key or settings.E2B_API_KEY):
        return "No E2B API key is configured."
    if not settings.SANDBOX_PROXY_URL:
        return "SANDBOX_PROXY_URL is not set; sandboxes need the control-plane proxy."
    return None


def sandbox_available(api_key: Optional[str] = None) -> bool:
    return sandbox_unavailable_reason(api_key) is None


# --- token index and handles ----------------------------------------------------


def _index_key(run_id: Any) -> str:
    return f"dw_sandbox:{run_id}"


async def _token_for(run_id: str, tenant_id: str, user_id: str, worker_id: str) -> str:
    """The live proxy token of this (run, worker), minted once and reused when the sandbox is recreated."""
    from app.services.sandbox_tokens import create_sandbox_token, validate_sandbox_token

    redis = await get_redis_service()
    key = _index_key(run_id)
    existing = (await redis.hgetall(key) or {}).get(worker_id)
    if existing and await validate_sandbox_token(existing) is not None:
        return existing
    token = await create_sandbox_token(tenant_id, user_id, run_id, worker_id)
    await redis.hset(key, {worker_id: token})
    await redis.expire(key, _INDEX_TTL)
    return token


async def get_handle(run_id: str, tenant_id: str, user_id: str, worker_id: str, api_key: Optional[str]):
    """The sandbox of ``(run, worker)``: reused, reconnected or created with a proxy token, never a real key."""
    proxy_url = get_settings().SANDBOX_PROXY_URL
    if not proxy_url:
        raise RuntimeError("SANDBOX_PROXY_URL is not set; refusing to start a sandbox without the control-plane proxy.")
    token = await _token_for(run_id, tenant_id, user_id, worker_id)
    env_vars = {
        "OPENAI_API_KEY": token,
        "OPENAI_BASE_URL": f"{proxy_url}/api/v1/sandbox-proxy/v1",
        "SESSION_ID": run_id,
    }
    handle = await get_sandbox_executor().get_or_create(
        session_id=run_id, worker_id=worker_id, api_key=api_key, env_vars=env_vars,
    )
    handle.token = token  # revoked when the handle is killed
    return handle


# --- teardown -------------------------------------------------------------------


async def _revoke(run_id: str, workers: Optional[Iterable[str]] = None) -> List[str]:
    """Revoke indexed tokens (all, or those of ``workers``); returns the workers whose token was live."""
    from app.services.sandbox_tokens import revoke_sandbox_token

    redis = await get_redis_service()
    key = _index_key(run_id)
    only = set(workers) if workers is not None else None
    revoked: List[str] = []
    for worker_id, token in (await redis.hgetall(key) or {}).items():
        if only is not None and worker_id not in only:
            continue
        if token:
            await revoke_sandbox_token(token)
            await redis.hset(key, {worker_id: ""})
            revoked.append(worker_id)
    if only is None:
        await redis.delete(key)
    return revoked


async def _kill_remote(key: str) -> None:
    """Kill a sandbox another pod created (known only from the Redis registry)."""
    from app.services.sandbox_executor import _redis_delete_sandbox, _redis_get_sandbox

    meta = await _redis_get_sandbox(key)
    if not meta or not meta.get("sandbox_id"):
        return
    try:
        from e2b_code_interpreter import AsyncSandbox

        sandbox = await AsyncSandbox.connect(meta["sandbox_id"], api_key=get_settings().E2B_API_KEY or None)
        await sandbox.kill()
    except Exception:
        logger.warning("Could not kill remote sandbox for %s; its TTL will", key)
    await _redis_delete_sandbox(key, owner_only=False)


async def _teardown_one(run_id: str, worker_id: str) -> None:
    executor = get_sandbox_executor()
    key = f"{run_id}:{worker_id}"
    if key in executor._sandboxes:
        await executor.teardown(run_id, worker_id)
    else:
        await _kill_remote(key)


async def teardown_worker(run_id: Any, worker_id: str) -> None:
    """Kill one worker's sandbox and revoke its token. Best effort; safe to repeat."""
    if not get_settings().ENABLE_SANDBOX:
        return
    run_id = str(run_id)
    for step in (lambda: _teardown_one(run_id, worker_id), lambda: _revoke(run_id, [worker_id])):
        try:
            await step()
        except Exception:
            logger.warning("Sandbox teardown step failed for %s:%s", run_id, worker_id, exc_info=True)


async def teardown_all(run_id: Any) -> None:
    """Kill every sandbox of a run and revoke every outstanding token (cancel, reaper). Safe to repeat."""
    if not get_settings().ENABLE_SANDBOX:
        return
    run_id = str(run_id)
    try:
        redis = await get_redis_service()
        workers = set((await redis.hgetall(_index_key(run_id)) or {}).keys())
    except Exception:
        logger.warning("Could not read the sandbox index of run %s", run_id, exc_info=True)
        workers = set()
    for worker_id in sorted(workers):
        try:
            await _teardown_one(run_id, worker_id)
        except Exception:
            logger.warning("Sandbox teardown failed for %s:%s", run_id, worker_id, exc_info=True)
    try:
        await get_sandbox_executor().teardown_session(run_id)  # anything local the index did not know
        await _revoke(run_id)
    except Exception:
        logger.warning("Sandbox session teardown failed for run %s", run_id, exc_info=True)


async def reap_if_terminal(factory: dws.SessionFactory, tenant_id: Any, conversation_id: Any) -> bool:
    """Lazy reaper: when the run has ended, tear down whatever it left behind. Returns True if it did."""
    run = await dws.load_run(factory, tenant_id, conversation_id)
    if run is None or run.status == "running":
        return False
    await teardown_all(run.id)
    return True


# --- tool bodies ----------------------------------------------------------------


def _outcome(result: Dict[str, Any]) -> ToolOutcome:
    if result.get("error"):
        return et._err(str(result["error"]), "sandbox_error", status=result.get("status"))
    text = json.dumps(result, ensure_ascii=False)
    if len(text) > MAX_OUTPUT_CHARS:
        trimmed = {**result, "result": str(result.get("result", ""))[:MAX_OUTPUT_CHARS], "truncated": True}
        text = json.dumps(trimmed, ensure_ascii=False)
    return ToolOutcome(content=text, is_error=False, details={})


async def _run(ctx: SkillContext, tool: str, args: Dict[str, Any], worker_id: str):
    """Authorize against the run, resolve the sandbox, execute. Returns ``(run, result-or-outcome)``."""
    tenant_id, conv_id, factory = et._scope(ctx)
    run = await dws.load_run(factory, tenant_id, conv_id)
    if (refusal := et._refusal(run)) is not None:
        return run, refusal
    worker_id = worker_id or OUTPUT_WORKER
    if worker_id != OUTPUT_WORKER:
        if not et.WORKER_ID.match(worker_id) or et._find_worker(run, worker_id) is None:
            return run, et._err(
                f"Worker {worker_id!r} is not registered. Use the worker id from your instructions.", "unknown_worker",
            )
    api_key = (ctx.api_keys or {}).get("e2b") or get_settings().E2B_API_KEY or None
    if api_key is None and ctx.user_id:
        from app.services.sandbox_executor import resolve_e2b_key

        api_key = await resolve_e2b_key(tenant_id, ctx.user_id)
    if reason := sandbox_unavailable_reason(api_key):
        return run, et._err(f"{reason} Continue without the sandbox.", "sandbox_unavailable")
    try:
        handle = await get_handle(str(run.id), str(tenant_id), str(ctx.user_id), worker_id, api_key)
    except Exception as exc:
        logger.warning("Deep Work sandbox could not start: %s", exc)
        return run, et._err(f"The sandbox could not start: {exc}. Continue without it.", "sandbox_failed")
    artifact_ctx = {"tenant_id": str(tenant_id), "session_id": str(run.id), "user_id": str(ctx.user_id)}
    result = await execute_sandbox_tool(_LEGACY_NAME.get(tool, tool), args, handle, artifact_ctx=artifact_ctx)
    return run, result


@skill(
    name="shell_exec",
    description=(
        "Run a shell command in your sandbox (python3, pandas, numpy, matplotlib, requests ...). Files live under "
        "/workspace and may be gone if the sandbox was restarted; regenerate them if so."
    ),
    category="deep_work",
    author="oniva",
)
async def shell_exec(ctx: SkillContext, command: str, worker_id: Optional[str] = None) -> ToolOutcome:
    """
    Args:
        command: Shell command, e.g. 'python3 /workspace/analyze.py'.
        worker_id: Your worker id; omit it when you are the output agent.
    """
    _, result = await _run(ctx, "shell_exec", {"command": command}, worker_id or OUTPUT_WORKER)
    return result if isinstance(result, ToolOutcome) else _outcome(result)


@skill(
    name="dw_browser_navigate",
    description="Open a URL in a headless browser inside your sandbox and extract content from the rendered page.",
    category="deep_work",
    author="oniva",
)
async def dw_browser_navigate(ctx: SkillContext, url: str, task: Optional[str] = None, worker_id: Optional[str] = None) -> ToolOutcome:
    """
    Args:
        url: http/https URL.
        task: What to extract (default: main text content).
        worker_id: Your worker id; omit it when you are the output agent.
    """
    args = {"url": url, **({"task": task} if task else {})}
    _, result = await _run(ctx, "dw_browser_navigate", args, worker_id or OUTPUT_WORKER)
    return result if isinstance(result, ToolOutcome) else _outcome(result)


@skill(
    name="dw_browser_screenshot",
    description="Screenshot a URL with headless Chromium in your sandbox; saves a PNG under /workspace.",
    category="deep_work",
    author="oniva",
)
async def dw_browser_screenshot(
    ctx: SkillContext, url: str, full_page: bool = True, filename: str = "screenshot.png", worker_id: Optional[str] = None,
) -> ToolOutcome:
    """
    Args:
        url: http/https URL.
        full_page: Capture the full scrollable page.
        filename: Output file name under /workspace (no slashes).
        worker_id: Your worker id; omit it when you are the output agent.
    """
    args = {"url": url, "full_page": full_page, "filename": filename}
    _, result = await _run(ctx, "dw_browser_screenshot", args, worker_id or OUTPUT_WORKER)
    return result if isinstance(result, ToolOutcome) else _outcome(result)


@skill(
    name="inspect_artifact",
    description="Inspect a generated file in the output sandbox (size, page count, problems). Use it before publish_artifact.",
    category="deep_work",
    author="oniva",
)
async def inspect_artifact(ctx: SkillContext, path: str) -> ToolOutcome:
    """
    Args:
        path: File path in the sandbox, e.g. '/workspace/report.pdf'.
    """
    _, result = await _run(ctx, "inspect_artifact", {"path": path}, OUTPUT_WORKER)
    return result if isinstance(result, ToolOutcome) else _outcome(result)


@skill(
    name="publish_artifact",
    description=(
        "Publish a finished deliverable from the output sandbox to storage and show it to the user. Call only "
        "after inspect_artifact passes. Publishing a file with the same name replaces the earlier one."
    ),
    category="deep_work",
    author="oniva",
)
async def publish_artifact(
    ctx: SkillContext, path: str, title: str, artifact_type: str,
    description: str = "", tags: Optional[List[str]] = None,
) -> ToolOutcome:
    """
    Args:
        path: File path in the sandbox, e.g. '/workspace/report.pdf'.
        title: Human-readable title.
        artifact_type: pdf_report, presentation, spreadsheet, dashboard, chart, document or data_export.
        description: Short description of the deliverable.
        tags: Optional tags.
    """
    args = {"path": path, "title": title, "artifact_type": artifact_type, "description": description, "tags": tags or []}
    run, result = await _run(ctx, "publish_artifact", args, OUTPUT_WORKER)
    if isinstance(result, ToolOutcome):
        return result
    if result.get("status") != "published":
        return _outcome(result)
    filename, size = result["filename"], result.get("size_bytes", 0)
    file_part = {
        "type": "file", "filename": filename, "size": size, "fileType": _guess_mime_type(filename),
        "downloadUrl": result["download_url"], "title": title, "description": description,
        "artifactType": artifact_type, "artifactUri": result["artifact_uri"], "tags": tags or [], "pageCount": None,
    }
    tenant_id, conv_id, factory = et._scope(ctx)
    await dws.replace_artifact(factory, tenant_id, conv_id, file_part)
    await teardown_worker(run.id, OUTPUT_WORKER)
    payload = {
        "status": "published", "artifact_uri": result["artifact_uri"], "download_url": result["download_url"],
        "filename": filename, "size": size, "file_part": file_part,
    }
    return ToolOutcome(content=json.dumps(payload, ensure_ascii=False), is_error=False, details={})
