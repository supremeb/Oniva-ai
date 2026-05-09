"""Deep Work Worker — focused research agent with tool calling."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.services.deep_work.event_sink import EventSink
from app.services.deep_work.models import Learning, WorkerResult, WorkerTask
from app.services.deep_work.sandbox_tools import (
    SANDBOX_TOOL_NAMES,
    execute_sandbox_tool,
)
from app.services.llm_gateway import LLMGateway, LLMRequest, LLMResponse
from app.services.session_storage import SessionStorage

if TYPE_CHECKING:
    from app.services.sandbox_executor import SandboxHandle

logger = logging.getLogger(__name__)

# Max chars for compressed tool results injected back into conversation
_MAX_RESULT_CHARS = 4000


class DeepWorkWorker:
    """Iterative tool-calling research worker."""

    def __init__(
        self,
        gateway: LLMGateway,
        tenant_id: str,
        user_id: str,
        session_storage: SessionStorage,
        session_id: str,
        worker_model: str,
        extractor_model: str,
        api_semaphore: asyncio.Semaphore,
        api_keys: dict[str, str] | None = None,
        sandbox: SandboxHandle | None = None,
    ):
        self._gateway = gateway
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._session_storage = session_storage
        self._session_id = session_id
        self._worker_model = worker_model
        self._extractor_model = extractor_model
        self._api_semaphore = api_semaphore
        self._api_keys = api_keys or {}
        self._sandbox = sandbox
        self._worker_prompt = _load_prompt("worker.md")
        self._extractor_prompt = _load_prompt("extractor.md")
        # Track last 3 tool call descriptions for per-worker activity cards
        self._recent_actions: list[str] = []

    async def _llm_call(self, request: LLMRequest) -> LLMResponse:
        """All LLM calls go through the shared semaphore."""
        async with self._api_semaphore:
            return await self._gateway.complete(
                request,
                tenant_id=UUID(self._tenant_id),
                user_id=UUID(self._user_id),
            )

    async def research(
        self,
        task: WorkerTask,
        tools_schema: list[dict],
        event_sink: EventSink,
    ) -> WorkerResult:
        """Execute focused research for a single sub-query."""
        learnings: list[Learning] = []
        sources: list[str] = []
        from datetime import date
        prompt = self._worker_prompt.replace("{{CURRENT_DATE}}", date.today().strftime("%B %d, %Y"))
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Research this: {task.sub_query}"},
        ]
        consecutive_failures = 0
        tool_call_count = 0
        iterations_used = 0

        for iteration in range(task.max_iterations):
            iterations_used = iteration + 1

            # Recitation: remind worker of mission
            messages.append({
                "role": "user",
                "content": (
                    f"[Recite] Sub-query: {task.sub_query} | "
                    f"Learnings: {len(learnings)} | "
                    f"Iteration: {iterations_used}/{task.max_iterations}"
                ),
            })

            response = await self._llm_call(LLMRequest(
                messages=messages,
                model=self._worker_model,
                tools=tools_schema,
                tool_choice="auto",
            ))

            # Add assistant message to conversation
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.content or "",
            }
            if response.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in response.tool_calls
                ]
            messages.append(assistant_msg)

            if not response.tool_calls:
                break  # Worker done

            for tool_call in response.tool_calls:
                # Emit activity event before tool execution
                activity_desc = _describe_tool_call(
                    tool_call.name, tool_call.arguments,
                )
                # Track in FIFO recent actions list (max 3)
                self._recent_actions.append(activity_desc)
                if len(self._recent_actions) > 3:
                    self._recent_actions = self._recent_actions[-3:]
                await self._emit_activity(
                    event_sink, task.worker_id, "tool_call", activity_desc,
                )

                result = await self._execute_tool(tool_call.name, tool_call.arguments)
                tool_call_count += 1

                # Capture screenshot from browser tool results
                if tool_call.name in ("browser_screenshot", "browser_navigate") and result.get("status") == "success":
                    await self._capture_screenshot(
                        event_sink, task.worker_id,
                        result, tool_call.name, tool_call.arguments,
                    )

                if result.get("error"):
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        break
                else:
                    consecutive_failures = 0
                    sources.append(result.get("url", tool_call.name))

                # Extraction (tier: low) — failure doesn't crash worker
                try:
                    new_learnings = await self._extract_learnings(
                        tool_call.name, result, task.sub_query,
                    )
                    learnings.extend(new_learnings)
                    if new_learnings:
                        await self._emit_activity(
                            event_sink, task.worker_id, "findings",
                            f"Found {len(new_learnings)} finding{'s' if len(new_learnings) != 1 else ''} "
                            f"({len(learnings)} total)",
                        )
                except Exception:
                    logger.warning(
                        "Extraction failed for %s in worker %s, skipping",
                        tool_call.name, task.worker_id,
                    )

                # Inject compressed result back into conversation
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": self._compress_result(result, learnings[-3:]),
                })

                # 2-Action Rule: persist every 2 tool calls
                if tool_call_count % 2 == 0:
                    await self._persist_learnings(task.worker_id, learnings)

            if consecutive_failures >= 3:
                break
            from app.config import get_settings
            if len(learnings) >= get_settings().DEEP_WORK_MAX_LEARNINGS_PER_WORKER:
                break
            # Diminishing returns: stop if recent sources overlap with earlier ones
            if len(learnings) >= 10:
                recent_urls = {l.source_url for l in learnings[-3:] if l.source_url}
                older_urls = {l.source_url for l in learnings[:-3] if l.source_url}
                if len(recent_urls & older_urls) >= 2:
                    logger.info("Worker %s stopping: revisiting same sources", task.worker_id)
                    break

        # Final persist
        await self._persist_learnings(task.worker_id, learnings)

        return WorkerResult(
            worker_id=task.worker_id,
            sub_query=task.sub_query,
            learnings=learnings,
            sources=list(set(sources)),
            confidence=min(len(learnings) / 5.0, 1.0),
            iterations_used=iterations_used,
        )

    # ── Tool Execution ──────────────────────────────────────────

    async def _execute_tool(
        self, tool_name: str, arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a skill tool and return the result."""
        # Sandbox tools: route through SandboxToolRouter
        if tool_name in SANDBOX_TOOL_NAMES and self._sandbox:
            return await execute_sandbox_tool(
                tool_name, arguments, self._sandbox, self._api_keys,
            )

        from app.agents.skills.registry import SkillRegistry
        from app.agents.skills.context import SkillContext

        skill = SkillRegistry.get_skill(tool_name)
        if not skill:
            return {"error": f"Unknown tool: {tool_name}"}

        try:
            ctx = SkillContext.create_minimal(
                user_id=self._user_id,
                tenant_id=self._tenant_id,
                api_keys=self._api_keys,
            )
            result = await skill.handler(ctx, **arguments)
            if isinstance(result, dict):
                return result
            return {"result": str(result)}
        except Exception as e:
            logger.warning("Tool %s failed: %s", tool_name, e)
            return {"error": str(e)}

    # ── Extraction ──────────────────────────────────────────────

    async def _extract_learnings(
        self, tool_name: str, result: dict, sub_query: str,
    ) -> list[Learning]:
        """Extract structured learnings from a tool result."""
        from app.services.deep_work.models import Learning

        # Inject schema into extractor prompt
        schema_json = json.dumps(Learning.model_json_schema(), indent=2)
        prompt = self._extractor_prompt.replace("{schema}", schema_json)

        # Compress the raw result for extraction
        result_text = json.dumps(result)[:_MAX_RESULT_CHARS]

        response = await self._llm_call(LLMRequest(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": (
                    f"Tool: {tool_name}\n"
                    f"Sub-query: {sub_query}\n"
                    f"Result:\n{result_text}"
                )},
            ],
            model=self._extractor_model,
            temperature=0.1,
            max_tokens=1500,
        ))

        return self._parse_learnings(response.content, sub_query)

    def _parse_learnings(self, content: str, sub_query: str) -> list[Learning]:
        """Parse Learning objects from extractor LLM output."""
        import re

        # Try direct JSON array parse
        try:
            data = json.loads(content)
            if isinstance(data, list):
                return [
                    Learning(**{**item, "sub_query": sub_query})
                    for item in data
                ]
        except (json.JSONDecodeError, Exception):
            pass

        # Try extracting JSON from code fence
        match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", content, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                return [
                    Learning(**{**item, "sub_query": sub_query})
                    for item in data
                ]
            except Exception:
                pass

        # Try extracting bare array
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                return [
                    Learning(**{**item, "sub_query": sub_query})
                    for item in data
                ]
            except Exception:
                pass

        return []

    # ── Activity Emission ──────────────────────────────────────

    async def _emit_activity(
        self,
        event_sink: EventSink,
        worker_id: str,
        activity_type: str,
        description: str,
    ) -> None:
        """Emit a worker activity event for real-time UI updates."""
        await event_sink.emit("deep_work_engine_worker_activity", {
            "worker_id": worker_id,
            "activity_type": activity_type,
            "description": description,
            "recent_actions": list(self._recent_actions),
        })

    # ── Screenshot Capture ──────────────────────────────────────

    async def _capture_screenshot(
        self,
        event_sink: EventSink,
        worker_id: str,
        result: dict[str, Any],
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        """Capture screenshot from browser tool result and emit SSE event."""
        import time as _time

        # browser_screenshot returns file_path directly
        file_path = result.get("file_path")
        page_url = result.get("url", arguments.get("url", ""))

        if not file_path or not self._sandbox:
            return

        try:
            # Read screenshot from sandbox (binary format for images)
            raw = await self._sandbox._sandbox.files.read(file_path, format='bytes')
            screenshot_data = raw if isinstance(raw, bytes) else raw.encode("utf-8")

            if not screenshot_data or len(screenshot_data) < 100:
                return  # Skip empty/invalid screenshots

            # Save to session workspace
            timestamp = int(_time.time() * 1000)
            filename = f"{worker_id}_{timestamp}.jpg"
            workspace_path = f"workspace/screenshots/{filename}"

            await self._session_storage.write_binary(
                self._tenant_id, self._session_id, workspace_path, screenshot_data,
            )

            # Generate URL for the screenshot
            screenshot_url = self._session_storage.get_screenshot_url(
                self._session_id, f"screenshots/{filename}",
            )

            await event_sink.emit("sandbox_screenshot", {
                "worker_id": worker_id,
                "url": screenshot_url,
                "page_url": page_url,
                "action": tool_name,
                "timestamp": timestamp,
            })
        except Exception:
            logger.debug("Screenshot capture failed for worker %s", worker_id)

    # ── Compression & Persistence ───────────────────────────────

    def _compress_result(
        self, result: dict, recent_learnings: list[Learning],
    ) -> str:
        """Compress tool result for conversation context (max 2000 chars)."""
        # Prefer learnings summary over raw result
        if recent_learnings:
            summary_parts = [
                f"Extracted {len(recent_learnings)} finding(s):"
            ]
            for l in recent_learnings:
                summary_parts.append(f"- {l.fact[:200]}")
            summary = "\n".join(summary_parts)
            if len(summary) <= _MAX_RESULT_CHARS:
                return summary

        # Fallback: truncate raw result
        raw = json.dumps(result)
        if len(raw) > _MAX_RESULT_CHARS:
            return raw[:_MAX_RESULT_CHARS] + "... [truncated]"
        return raw

    async def _persist_learnings(
        self, worker_id: str, learnings: list[Learning],
    ) -> None:
        """Persist worker learnings to session filesystem."""
        try:
            await self._session_storage.write(
                self._tenant_id,
                self._session_id,
                f"workers/{worker_id}_learnings.json",
                json.dumps([l.model_dump() for l in learnings]),
            )
        except Exception:
            logger.warning("Failed to persist learnings for worker %s", worker_id)


# ── Module-level helpers ────────────────────────────────────────

from pathlib import Path

_PROMPT_CACHE: dict[str, str] = {}


def _load_prompt(filename: str) -> str:
    if filename not in _PROMPT_CACHE:
        _PROMPT_CACHE[filename] = (
            Path(__file__).parent / "prompts" / filename
        ).read_text()
    return _PROMPT_CACHE[filename]


def _describe_tool_call(tool_name: str, arguments: dict) -> str:
    """Build a concise human-readable description of a tool call."""
    if tool_name == "shell_exec":
        cmd = arguments.get("command", "")
        return f"Running: {cmd[:80]}" if cmd else "Running shell command..."
    if tool_name == "web_search":
        query = arguments.get("query", "")
        return f"Searching: {query[:80]}" if query else "Searching the web..."
    if tool_name == "browse_url":
        url = arguments.get("url", "")
        # Show domain only for readability
        from urllib.parse import urlparse
        try:
            domain = urlparse(url).netloc
        except Exception:
            domain = url[:60]
        return f"Reading: {domain}" if domain else "Browsing a page..."
    if tool_name == "search_knowledge":
        query = arguments.get("query", "")
        return f"Searching knowledge base: {query[:60]}" if query else "Searching knowledge..."
    if tool_name == "read_file":
        path = arguments.get("path", arguments.get("file_path", ""))
        return f"Reading file: {path[:60]}" if path else "Reading a file..."
    if tool_name == "browser_navigate":
        url = arguments.get("url", "")
        from urllib.parse import urlparse
        try:
            domain = urlparse(url).netloc
        except Exception:
            domain = url[:60]
        return f"Navigating browser: {domain}" if domain else "Navigating browser..."
    if tool_name == "browser_screenshot":
        url = arguments.get("url", "")
        return f"Taking screenshot: {url[:60]}" if url else "Taking screenshot..."
    # Generic fallback
    return f"Running {tool_name}..."
