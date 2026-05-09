"""
Autonomous Executor for Deep Work Mode

Iterative agent loop that runs multiple steps autonomously after plan approval.
Manages the lifecycle of a Deep Work session: create session, execute loop,
handle pause/resume/stop, and stream SSE events.

Flow:
1. Create session (plan.md, todo.md, state.json)
2. Loop: recite -> build prompt -> call LLM -> execute tool -> stream SSE -> check exit
3. Finalize: save artifacts, stream summary
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from app.config import get_settings
from app.services.redis_ha import RedisHAService

logger = logging.getLogger(__name__)
settings = get_settings()


class AutonomousExecutor:
    """
    Iterative agent loop for Deep Work execution.

    Dependencies:
    - SessionStorage: filesystem sessions for plan.md, todo.md, workspace
    - SkillRegistry: tool functions the agent can call
    - LLMGateway: multi-provider LLM calls
    - sse_queue: asyncio.Queue for streaming events to the SSE endpoint
    """

    # Budget limits from config
    MAX_TOOL_CALLS = getattr(settings, "DEEP_WORK_MAX_TOOL_CALLS", 100)
    MAX_DURATION_SEC = getattr(settings, "DEEP_WORK_MAX_DURATION_SEC", 1800)

    # Context compression
    COMPRESSION_THRESHOLD = 10  # Compress after N tool calls
    KEEP_RECENT = 5  # Keep last N tool results in full

    def __init__(
        self,
        session_storage,
        skill_registry,
        llm_gateway,
        sse_queue: asyncio.Queue,
    ):
        self.session_storage = session_storage
        self.skill_registry = skill_registry
        self.llm_gateway = llm_gateway
        self.sse_queue = sse_queue

        # Session state
        self._session_id: Optional[str] = None
        self._agent_config: Optional[Any] = None
        self._tenant_id: Optional[str] = None
        self._user_id: Optional[str] = None

        # Execution state
        self._message_history: List[Dict[str, Any]] = []
        self._tool_call_count = 0
        self._no_tool_call_count = 0
        self._start_time: Optional[float] = None
        self._total_steps = 0

        # Control flags
        self._exit_flag = False
        self._pause_flag = False
        self._paused_event = asyncio.Event()
        self._paused_event.set()  # Not paused initially

        # Human input
        self._human_input_event = asyncio.Event()
        self._human_input_response: Optional[str] = None

        # Tool map
        self._tools: Dict[str, Callable] = {}

        # Workspace file tracking for change detection
        self._workspace_snapshot: Dict[str, float] = {}  # path -> mtime

    async def start_session(
        self,
        agent_config: Any,
        plan: Dict[str, Any],
        todo_items: List[str],
        message: str,
        user_id: str,
        tenant_id: str,
        session_id: str | None = None,
        conversation_id: str | None = None,
    ) -> str:
        """Create session, initialize files, return session_id."""
        session_id = session_id or str(uuid.uuid4())
        self._session_id = session_id
        self._agent_config = agent_config
        self._user_id = user_id
        self._tenant_id = tenant_id
        self._total_steps = len(plan.get("steps", []))

        # Initialize session files
        plan_content = f"# {plan['title']}\n\n"
        for i, step in enumerate(plan.get("steps", []), 1):
            plan_content += f"{i}. {step}\n"

        todo_content = "\n".join(f"- [ ] {item}" for item in todo_items)

        state = {
            "session_id": session_id,
            "conversation_id": conversation_id,
            "status": "executing",
            "current_step": 0,
            "total_steps": self._total_steps,
            "created_at": time.time(),
            "original_message": message,
            "plan": plan,
            "todos": [{"text": item, "status": "pending"} for item in todo_items],
            "sources": [],
            "connectors": [],
            "summary": None,
            "key_findings": [],
        }

        await self.session_storage.write(self._tenant_id, session_id, "plan.md", plan_content)
        await self.session_storage.write(self._tenant_id, session_id, "todo.md", todo_content)
        await self.session_storage.write(
            self._tenant_id, session_id, "state.json", json.dumps(state, indent=2)
        )

        # Initialize message history with the original user message
        self._message_history = [
            {"role": "user", "content": message},
        ]

        # Load tools
        self._load_tools()

        # Initialize workspace snapshot for change detection
        self._workspace_snapshot = await self._scan_workspace(session_id)

        return session_id

    # Default skills loaded when no agent_config is provided (general assistant)
    DEFAULT_GENERAL_SKILLS = [
        "write_file", "read_file", "list_workspace",
        "web_search", "execute_python",
    ]

    def _load_tools(self):
        """Load tool functions from the skill registry."""
        # Planning skills are always available
        planning_skills = ["present_plan", "create_task", "update_task", "list_tasks", "ask_user", "complete_task"]

        # Agent's configured skills (or defaults for general assistant)
        agent_skills = []
        if self._agent_config:
            agent_skills = getattr(self._agent_config, "skills", []) or []
        else:
            agent_skills = list(self.DEFAULT_GENERAL_SKILLS)

        all_skill_names = list(set(planning_skills + agent_skills))

        for skill_name in all_skill_names:
            skill = self.skill_registry.get_skill(skill_name)
            if skill and skill.handler:
                self._tools[skill_name] = skill.handler

    async def execute(self, session_id: str) -> None:
        """Main agent loop. Runs until exit condition."""
        self._session_id = session_id
        self._start_time = time.time()
        self._exit_flag = False
        self._pause_flag = False

        # Stream start event
        plan_content = await self.session_storage.read(self._tenant_id, session_id, "plan.md") or ""
        todo_content = await self.session_storage.read(self._tenant_id, session_id, "todo.md") or ""

        await self._stream_event("deep_work_start", {
            "session_id": session_id,
            "plan": plan_content,
            "todos": self._parse_todos(todo_content),
            "agent_id": str(getattr(self._agent_config, "id", "")),
        })

        iteration = 0
        try:
            while not await self._check_exit():
                # Check pause
                if await self._check_pause():
                    await self._stream_event("deep_work_paused", {
                        "reason": "user_requested",
                        "resumable": True,
                    })
                    self._paused_event.clear()
                    # Wait for in-memory resume signal or Redis pause key removal
                    while await self._check_pause():
                        try:
                            await asyncio.wait_for(self._paused_event.wait(), timeout=2)
                        except asyncio.TimeoutError:
                            pass
                    self._pause_flag = False

                iteration += 1
                should_continue = await self._iterate(session_id, iteration)
                if not should_continue:
                    break

        except Exception as e:
            logger.exception(f"Deep Work execution error: {e}")
            await self._stream_event("deep_work_error", {
                "error": str(e),
                "last_step": iteration,
                "can_retry": True,
            })
            # Update state
            await self._update_state(session_id, {"status": "error"})

    async def _iterate(self, session_id: str, iteration: int) -> bool:
        """Single iteration. Returns True to continue, False to exit."""
        step_start = time.time()

        # 1. RECITE -- read plan.md + tasks.json (or todo.md fallback)
        plan = await self.session_storage.read(self._tenant_id, session_id, "plan.md") or ""
        todo = await self.session_storage.read(self._tenant_id, session_id, "todo.md") or ""

        # Prefer tasks.json over todo.md for recitation
        task_section = ""
        try:
            tasks_content = await self.session_storage.read(
                self._tenant_id, session_id, "tasks.json"
            )
            if tasks_content:
                tasks_data = json.loads(tasks_content)
                task_lines = [f"- [{t['status']}] {t['title']}" for t in tasks_data]
                task_section = "## Tasks\n" + "\n".join(task_lines)
        except Exception:
            pass
        if not task_section:
            task_section = f"## Todo (Progress)\n{todo}"

        # 2. BUILD PROMPT
        messages = self._build_messages(plan, task_section, iteration)

        # 3. CALL LLM
        try:
            from app.services.llm_gateway import LLMRequest

            model = getattr(self._agent_config, "model", None) or "gpt-4o"
            system_prompt = getattr(self._agent_config, "system_prompt", "") or ""

            # Build tool descriptions for the LLM
            tool_descriptions = self._build_tool_descriptions()

            # Add system context with tools
            system_content = self._build_system_prompt(system_prompt, tool_descriptions)
            full_messages = [{"role": "system", "content": system_content}] + messages

            llm_request = LLMRequest(
                messages=full_messages,
                model=model,
                temperature=0.7,
                agent_id=getattr(self._agent_config, "id", None),
            )

            response = await self.llm_gateway.complete(
                llm_request,
                tenant_id=uuid.UUID(self._tenant_id) if self._tenant_id else None,
                user_id=uuid.UUID(self._user_id) if self._user_id else None,
            )
        except Exception as e:
            # Include provider-level errors for debugging
            detail = str(e)
            if hasattr(e, "errors"):
                detail += " | Details: " + "; ".join(e.errors)
            logger.error(f"LLM call failed at iteration {iteration}: {detail}")
            await self._stream_event("deep_work_error", {
                "error": f"LLM call failed: {detail}",
                "last_step": iteration,
                "can_retry": True,
            })
            return False

        # 4. PARSE & EXECUTE
        content = response.content
        tool_call = self._extract_tool_call(content)

        if tool_call is None:
            # Agent responded with text only — re-prompt to use tools
            self._no_tool_call_count += 1
            self._message_history.append({"role": "assistant", "content": content})

            # Stream the text as a step
            elapsed_ms = int((time.time() - step_start) * 1000)
            await self._stream_event("step_complete", {
                "step_number": iteration,
                "total_steps": self._total_steps,
                "iteration": iteration,
                "tool_name": None,
                "status": "success",
                "result": None,
                "agent_message": content,
                "duration_ms": elapsed_ms,
            })

            if self._no_tool_call_count >= 5:
                # Auto-complete after 5 consecutive no-tool-call iterations
                await self._auto_complete(session_id, iteration)
                return False

            # Re-prompt the LLM to use a tool instead of text
            self._message_history.append({
                "role": "user",
                "content": (
                    "You must respond with a JSON tool call, not plain text. "
                    "Use write_file to save your work, update_task to mark progress, "
                    "or complete_task if all steps are done. "
                    'Format: {"tool": "<name>", "args": {...}}'
                ),
            })

            return self._check_exit_conditions()

        self._no_tool_call_count = 0

        # Execute tool
        tool_name = tool_call["name"]
        tool_args = tool_call.get("args", {})

        # Stream activity before execution so the UI shows what's happening
        activity_text = self._describe_activity(tool_name, tool_args)
        await self._stream_event("step_activity", {
            "tool_name": tool_name,
            "description": activity_text,
            "step_number": iteration,
        })

        # Track sources and connectors
        await self._track_source(session_id, tool_name, tool_args)

        result = await self._execute_tool(tool_name, tool_args)
        self._tool_call_count += 1

        # 5. OBSERVE -- append to message history
        # Use "user" role for tool results (not "tool") since we use text-based
        # tool calls, not native function calling. "tool" role requires
        # tool_call_id and breaks providers that don't support it.
        self._message_history.append({"role": "assistant", "content": content})
        result_str = json.dumps(result) if isinstance(result, dict) else str(result)
        self._message_history.append({
            "role": "user",
            "content": f"[Tool Result: {tool_name}]\n{result_str}",
        })

        # 5b. WORKSPACE -- detect file changes and emit SSE events
        await self._emit_workspace_changes(session_id)

        # 6. Check for special result types
        if isinstance(result, dict):
            result_type = result.get("type")

            if result_type == "human_input_required":
                # Pause for human input
                event_data = {
                    "question": result.get("question", ""),
                    "session_id": session_id,
                }
                # Forward structured fields if present
                if result.get("options"):
                    event_data["options"] = result["options"]
                    event_data["multiSelect"] = result.get("multi_select", False)
                await self._stream_event("human_input_required", event_data)
                # Wait for input (with timeout) — polls Redis and in-memory event
                self._human_input_event.clear()
                human_response = await self._poll_human_input(timeout=1800)
                if human_response:
                    self._message_history.append({
                        "role": "user",
                        "content": human_response,
                    })
                else:
                    self._message_history.append({
                        "role": "user",
                        "content": "[Timeout: No response received. Proceeding with best judgment.]",
                    })

            elif result_type == "complete":
                # Agent signaled completion
                elapsed_ms = int((time.time() - step_start) * 1000)
                await self._stream_event("step_complete", {
                    "step_number": self._total_steps,
                    "total_steps": self._total_steps,
                    "iteration": iteration,
                    "tool_name": tool_name,
                    "status": "success",
                    "result": result,
                    "agent_message": None,
                    "duration_ms": elapsed_ms,
                })

                total_time = int(time.time() - (self._start_time or time.time()))

                summary = result.get("summary", "")
                key_findings = result.get("key_findings", [])

                # Generate consolidated report
                await self._generate_final_report(session_id, summary, key_findings, total_time)
                artifacts = await self._list_workspace_files(session_id)

                await self._stream_event("deep_work_complete", {
                    "summary": summary,
                    "key_findings": key_findings,
                    "artifacts": artifacts,
                    "total_time": total_time,
                    "total_steps": self._tool_call_count,
                })
                await self._update_state(session_id, {
                    "status": "completed",
                    "summary": summary,
                    "key_findings": key_findings,
                })
                self._exit_flag = True
                return False

            # Check for todo update (embed in step_complete)
            if result.get("todos"):
                todos = result["todos"]
                completed_count = sum(
                    1 for t in todos if t.get("status") == "completed"
                )
                await self._stream_event("todo_update", {
                    "todos": todos,
                    "completed_count": completed_count,
                })
                await self._update_state(session_id, {
                    "todos": todos,
                    "current_step": completed_count,
                })

        # 7. STREAM step_complete
        elapsed_ms = int((time.time() - step_start) * 1000)
        await self._stream_event("step_complete", {
            "step_number": iteration,
            "total_steps": self._total_steps,
            "iteration": iteration,
            "tool_name": tool_name,
            "status": "success" if not (isinstance(result, dict) and result.get("error")) else "error",
            "result": result if isinstance(result, dict) else {"output": str(result)},
            "agent_message": None,
            "duration_ms": elapsed_ms,
        })

        # 8. COMPRESS -- after threshold, summarize older history
        if iteration > self.COMPRESSION_THRESHOLD:
            self._compress_history()

        # 9. CHECK EXIT
        return self._check_exit_conditions()

    def _build_messages(
        self, plan: str, task_section: str, iteration: int
    ) -> List[Dict[str, Any]]:
        """Build message list with recitation at the end."""
        messages = list(self._message_history)

        # Append recitation context at the end (KV-cache optimization: static prefix, dynamic suffix)
        recitation = (
            f"\n\n[CURRENT CONTEXT]\n"
            f"## Plan\n{plan}\n\n"
            f"{task_section}\n\n"
            f"Step {iteration} of ~{self._total_steps}. "
            f"Continue with the next incomplete step. "
            f"Call complete_task when all work is done."
        )

        if messages and messages[-1]["role"] == "user":
            messages[-1] = {
                **messages[-1],
                "content": messages[-1]["content"] + recitation,
            }
        else:
            messages.append({"role": "user", "content": recitation})

        return messages

    def _build_system_prompt(
        self, agent_prompt: str, tool_descriptions: str
    ) -> str:
        """Build the system prompt for autonomous execution."""
        return (
            f"{agent_prompt}\n\n"
            f"## Autonomous Execution Mode\n"
            f"You are executing a pre-approved plan autonomously.\n\n"
            f"**CRITICAL: You MUST respond with a JSON tool call on EVERY turn.**\n"
            f"Do NOT respond with plain text descriptions. Always call a tool.\n\n"
            f"### Workflow\n"
            f"1. For each step in the plan, use the appropriate tool to do the work\n"
            f"2. Use `write_file` to save outputs, analysis, and results to the workspace\n"
            f"3. Use `create_task` to plan work, `update_task` to mark progress, `list_tasks` to check status\n"
            f"4. When ALL steps are complete, call `complete_task` with a summary\n"
            f"5. If you need user input, call `ask_user`. When a request is ambiguous, use ask_user with structured options to clarify before guessing.\n\n"
            f"### Response Format\n"
            f"You MUST respond with ONLY a JSON tool call in this exact format:\n"
            f'{{"tool": "<tool_name>", "args": {{...}}}}\n\n'
            f"Examples:\n"
            f'{{"tool": "write_file", "args": {{"file_path": "analysis.md", "content": "# Analysis\\n\\nResults..."}}}}\n'
            f'{{"tool": "update_task", "args": {{"task_id": "task_abc12345", "status": "completed"}}}}\n'
            f'{{"tool": "complete_task", "args": {{"summary": "Completed analysis of...", "key_findings": ["Finding 1", "Finding 2"]}}}}\n\n'
            f"**Never respond with plain text. Always use a tool call.**\n\n"
            f"## Available Tools\n{tool_descriptions}"
        )

    def _build_tool_descriptions(self) -> str:
        """Build tool descriptions with parameter info for the system prompt."""
        import inspect

        lines = []
        for name, handler in self._tools.items():
            desc = getattr(handler, "__skill_description__", "")
            # Extract parameter names from the handler signature (skip 'ctx')
            try:
                sig = inspect.signature(handler)
                params = [
                    p.name for p in sig.parameters.values()
                    if p.name not in ("ctx", "args", "kwargs")
                ]
                param_str = f" — params: {', '.join(params)}" if params else ""
            except (ValueError, TypeError):
                param_str = ""
            lines.append(f"- **{name}**: {desc}{param_str}")
        return "\n".join(lines)

    def _extract_tool_call(self, content: str) -> Optional[Dict[str, Any]]:
        """
        Extract tool call from LLM response content.

        Looks for JSON tool call format in the response.
        Supports inline JSON, markdown code blocks, and nested JSON objects.
        """
        import re

        # Strip markdown code fences if present
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.split("\n")
            # Remove first line (```json) and last line (```)
            inner_lines = []
            for line in lines[1:]:
                if line.strip() == "```":
                    break
                inner_lines.append(line)
            stripped = "\n".join(inner_lines).strip()

        # Try parsing the entire (possibly stripped) content as JSON first
        for text in [stripped, content.strip()]:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    name = parsed.get("tool") or parsed.get("name")
                    args = parsed.get("args") or parsed.get("arguments", {})
                    if name and name in self._tools:
                        return {"name": name, "args": args}
            except (json.JSONDecodeError, ValueError):
                pass

        # Try to find a JSON object with "tool" key using balanced brace matching
        tool_match = re.search(r'\{\s*"tool"\s*:', content)
        if not tool_match:
            tool_match = re.search(r'\{\s*"name"\s*:', content)

        if tool_match:
            # Extract balanced JSON starting from the match
            start = tool_match.start()
            extracted = self._extract_balanced_json(content[start:])
            if extracted:
                try:
                    parsed = json.loads(extracted)
                    name = parsed.get("tool") or parsed.get("name")
                    args = parsed.get("args") or parsed.get("arguments", {})
                    if name and name in self._tools:
                        return {"name": name, "args": args}
                except (json.JSONDecodeError, ValueError):
                    pass

        return None

    @staticmethod
    def _extract_balanced_json(text: str) -> Optional[str]:
        """Extract the first balanced JSON object from text."""
        if not text or text[0] != "{":
            return None
        depth = 0
        in_string = False
        escape = False
        for i, ch in enumerate(text):
            if escape:
                escape = False
                continue
            if ch == "\\":
                if in_string:
                    escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[: i + 1]
        return None

    def _describe_activity(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """Generate a human-readable description of what the agent is about to do."""
        descriptions = {
            "web_search": lambda a: f"Searching for {a.get('query', 'information')}...",
            "browse_url": lambda a: f"Reading {a.get('url', 'webpage')}...",
            "write_file": lambda a: f"Writing to {a.get('file_path', a.get('path', 'file'))}...",
            "read_file": lambda a: f"Reading {a.get('file_path', a.get('path', 'file'))}...",
            "execute_python": lambda _: "Running Python code...",
            "analyze_code": lambda _: "Analyzing code...",
            "generate_chart": lambda _: "Generating chart...",
            "update_todo": lambda _: "Updating task list...",
            "create_task": lambda a: f"Creating task: {a.get('title', 'task')[:40]}...",
            "update_task": lambda a: f"Updating task {a.get('task_id', '')}...",
            "list_tasks": lambda _: "Listing tasks...",
            "complete_task": lambda _: "Completing task...",
            "fetch_market_data": lambda a: f"Fetching market data for {a.get('symbol', 'asset')}...",
            "search_documents": lambda a: f"Searching documents for {a.get('query', 'information')}...",
        }
        formatter = descriptions.get(tool_name)
        if formatter:
            try:
                return formatter(tool_args)
            except Exception:
                pass
        # Fallback: humanize tool name
        return f"Running {tool_name.replace('_', ' ')}..."

    async def _track_source(self, session_id: str, tool_name: str, tool_args: Dict[str, Any]) -> None:
        """Track web sources and connectors used, persist and stream to frontend."""
        source = None
        connector = None

        if tool_name == "web_search":
            query = tool_args.get("query", "")
            if query:
                source = {"type": "search", "title": query, "query": query}
        elif tool_name == "browse_url":
            url = tool_args.get("url", "")
            if url:
                # Extract domain for display
                try:
                    from urllib.parse import urlparse
                    domain = urlparse(url).netloc
                except Exception:
                    domain = url[:60]
                source = {"type": "url", "title": domain, "url": url, "domain": domain}
        elif tool_name in ("fetch_from_connector", "fetch_from_rest_api"):
            connector_name = tool_args.get("connector_id", tool_args.get("connector_name", tool_name))
            connector = {"name": str(connector_name)}
        elif tool_name == "fetch_market_data":
            connector = {"name": "Market Data"}
        elif tool_name == "search_documents":
            query = tool_args.get("query", "")
            source = {"type": "knowledge_base", "title": query or "Knowledge Base", "query": query}

        if source or connector:
            try:
                state_content = await self.session_storage.read(self._tenant_id, session_id, "state.json")
                state = json.loads(state_content) if state_content else {}
                sources = state.get("sources", [])
                connectors = state.get("connectors", [])

                if source and source not in sources:
                    sources.append(source)
                if connector and connector.get("name") not in [c.get("name") for c in connectors]:
                    connectors.append(connector)

                await self._update_state(session_id, {"sources": sources, "connectors": connectors})
                await self._stream_event("source_update", {"sources": sources, "connectors": connectors})
            except Exception:
                pass  # Non-critical

    async def _execute_tool(
        self, tool_name: str, tool_args: Dict[str, Any]
    ) -> Any:
        """Execute a tool by name with the given arguments."""
        handler = self._tools.get(tool_name)
        if not handler:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

        try:
            from app.agents.skills.context import SkillContext

            ctx = SkillContext(
                user_id=self._user_id or "unknown",
                tenant_id=self._tenant_id,
                session_id=self._session_id,
            )
            result = await handler(ctx, **tool_args)
            return result
        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}")
            return {"success": False, "error": str(e)}

    def _compress_history(self):
        """Compress older tool results to save context space."""
        if len(self._message_history) <= self.KEEP_RECENT * 2 + 2:
            return

        # Keep system message (index 0) and the first user message
        # Compress everything in the middle except the last KEEP_RECENT entries
        cutoff = len(self._message_history) - (self.KEEP_RECENT * 2)
        compressed = []

        for i, msg in enumerate(self._message_history[:cutoff]):
            if msg["role"] == "user" and i == 0:
                compressed.append(msg)
            elif msg["role"] == "user" and msg.get("content", "").startswith("[Tool Result:"):
                # Compress tool result messages
                content = msg.get("content", "")
                if len(content) > 200:
                    content = content[:200] + "..."
                compressed.append({
                    "role": "user",
                    "content": f"[Compressed] {content}",
                })
            else:
                # Keep assistant/user messages but truncate if long
                content = msg.get("content", "")
                if len(content) > 300:
                    content = content[:300] + "..."
                compressed.append({**msg, "content": content})

        self._message_history = compressed + self._message_history[cutoff:]

    def _check_exit_conditions(self) -> bool:
        """Check budget and exit conditions. Returns True to continue."""
        if self._exit_flag:
            return False

        if self._tool_call_count >= self.MAX_TOOL_CALLS:
            logger.info(f"Deep Work budget exceeded: {self._tool_call_count} tool calls")
            asyncio.create_task(self._stream_event("deep_work_paused", {
                "reason": "budget_exceeded",
                "resumable": False,
            }))
            return False

        elapsed = time.time() - (self._start_time or time.time())
        if elapsed >= self.MAX_DURATION_SEC:
            logger.info(f"Deep Work time limit exceeded: {elapsed:.0f}s")
            asyncio.create_task(self._stream_event("deep_work_paused", {
                "reason": "time_limit_exceeded",
                "resumable": False,
            }))
            return False

        return True

    async def _auto_complete(self, session_id: str, iteration: int):
        """Auto-complete when agent stops calling tools."""
        total_time = int(time.time() - (self._start_time or time.time()))
        summary = "Task auto-completed after agent stopped calling tools."

        await self._generate_final_report(session_id, summary, [], total_time)
        artifacts = await self._list_workspace_files(session_id)

        await self._stream_event("deep_work_complete", {
            "summary": summary,
            "key_findings": [],
            "artifacts": artifacts,
            "total_time": total_time,
            "total_steps": self._tool_call_count,
        })
        await self._update_state(session_id, {"status": "completed", "summary": summary})

    # ------------------------------------------------------------------
    # Redis-backed cross-process control signal routing
    # ------------------------------------------------------------------

    _CONTROL_TTL = 3600  # 1 hour TTL for control keys

    @staticmethod
    async def set_pause(session_id: str, paused: bool) -> None:
        """Set pause flag in Redis (cross-process)."""
        redis = RedisHAService()
        key = f"fleet:control:{session_id}:pause"
        if paused:
            await redis.setex(key, AutonomousExecutor._CONTROL_TTL, "1")
        else:
            await redis.delete(key)

    @staticmethod
    async def set_exit(session_id: str) -> None:
        """Set exit flag in Redis (cross-process)."""
        redis = RedisHAService()
        await redis.setex(f"fleet:control:{session_id}:exit", AutonomousExecutor._CONTROL_TTL, "1")

    @staticmethod
    async def send_input(session_id: str, text: str) -> None:
        """Push human input to Redis list (cross-process)."""
        redis = RedisHAService()
        key = f"fleet:input:{session_id}"
        await redis.lpush(key, text)
        await redis.expire(key, AutonomousExecutor._CONTROL_TTL)

    async def _check_exit(self) -> bool:
        """Check exit flag — Redis first, in-memory fallback."""
        if self._exit_flag:
            return True
        if not self._session_id:
            return False
        redis = RedisHAService()
        val = await redis.get(f"fleet:control:{self._session_id}:exit")
        return bool(val)

    async def _check_pause(self) -> bool:
        """Check pause flag — Redis first, in-memory fallback."""
        if self._pause_flag:
            return True
        if not self._session_id:
            return False
        redis = RedisHAService()
        val = await redis.get(f"fleet:control:{self._session_id}:pause")
        return bool(val)

    async def _poll_human_input(self, timeout: float = 1800) -> Optional[str]:
        """Poll Redis list for human input with a deadline. Falls back to in-memory event."""
        if not self._session_id:
            # Fallback: wait on the in-memory event
            try:
                await asyncio.wait_for(self._human_input_event.wait(), timeout=timeout)
                response = self._human_input_response
                self._human_input_response = None
                return response
            except asyncio.TimeoutError:
                return None

        redis = RedisHAService()
        key = f"fleet:input:{self._session_id}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            # Check Redis list first
            msgs = await redis.lrange(key, 0, 0)
            if msgs:
                await redis.delete(key)
                raw = msgs[0]
                return raw if isinstance(raw, str) else raw.decode()
            # Also check in-memory event (same-process send_human_input)
            if self._human_input_event.is_set():
                self._human_input_event.clear()
                response = self._human_input_response
                self._human_input_response = None
                return response
            await asyncio.sleep(1)
        return None

    # ------------------------------------------------------------------
    # Control methods (write to both in-memory and Redis)
    # ------------------------------------------------------------------

    async def pause(self, session_id: str) -> None:
        """Pause after current step completes."""
        self._pause_flag = True
        await AutonomousExecutor.set_pause(session_id, True)
        await self._update_state(session_id, {"status": "paused"})

    async def resume(self, session_id: str) -> None:
        """Resume paused session."""
        self._pause_flag = False
        self._paused_event.set()
        await AutonomousExecutor.set_pause(session_id, False)
        await self._update_state(session_id, {"status": "executing"})

    async def stop(self, session_id: str) -> None:
        """Graceful stop, save current state."""
        self._exit_flag = True
        self._paused_event.set()  # Unblock if paused
        self._human_input_event.set()  # Unblock if waiting
        await AutonomousExecutor.set_exit(session_id)

        total_time = int(time.time() - (self._start_time or time.time()))
        summary = "Task stopped by user."

        await self._generate_final_report(session_id, summary, [], total_time)
        artifacts = await self._list_workspace_files(session_id)

        await self._stream_event("deep_work_complete", {
            "summary": summary,
            "key_findings": [],
            "artifacts": artifacts,
            "total_time": total_time,
            "total_steps": self._tool_call_count,
        })
        await self._update_state(session_id, {"status": "stopped", "summary": summary})

    async def send_human_input(self, session_id: str, input_text: str) -> None:
        """Respond to ask_user, resume loop."""
        self._human_input_response = input_text
        self._human_input_event.set()
        await AutonomousExecutor.send_input(session_id, input_text)

    async def _stream_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Put an SSE event on the queue for the endpoint to stream."""
        event = {
            "type": event_type,
            "data": data,
            "id": str(uuid.uuid4()),
            "timestamp": time.time(),
        }
        await self.sse_queue.put(event)

    async def _update_state(
        self, session_id: str, updates: Dict[str, Any]
    ) -> None:
        """Update state.json in the session."""
        try:
            state_content = await self.session_storage.read(self._tenant_id, session_id, "state.json")
            state = json.loads(state_content) if state_content else {}
            state.update(updates)
            state["updated_at"] = time.time()
            await self.session_storage.write(
                self._tenant_id, session_id, "state.json", json.dumps(state, indent=2)
            )
        except Exception as e:
            logger.debug(f"Failed to update state: {e}")

    async def _scan_workspace(self, session_id: str) -> Dict[str, float]:
        """Scan workspace directory and return {relative_path: mtime} dict."""
        try:
            session_path = self.session_storage._get_session_path(self._tenant_id, session_id)
            workspace = session_path / "workspace"
            if not workspace.exists():
                return {}
            return {
                str(f.relative_to(workspace)): f.stat().st_mtime
                for f in workspace.rglob("*")
                if f.is_file()
            }
        except Exception:
            return {}

    async def _emit_workspace_changes(self, session_id: str) -> None:
        """Compare current workspace state against snapshot, emit events for changes."""
        current = await self._scan_workspace(session_id)
        previous = self._workspace_snapshot

        # Detect creates and updates
        for path, mtime in current.items():
            if path not in previous:
                # New file
                size = self._get_workspace_file_size(session_id, path)
                await self._stream_event("workspace_update", {
                    "action": "create",
                    "path": path,
                    "size": size,
                })
            elif mtime > previous[path]:
                # Modified file
                size = self._get_workspace_file_size(session_id, path)
                await self._stream_event("workspace_update", {
                    "action": "update",
                    "path": path,
                    "size": size,
                })

        # Detect deletes
        for path in previous:
            if path not in current:
                await self._stream_event("workspace_update", {
                    "action": "delete",
                    "path": path,
                })

        # Update snapshot
        self._workspace_snapshot = current

    def _get_workspace_file_size(self, session_id: str, relative_path: str) -> int:
        """Get size of a workspace file."""
        try:
            session_path = self.session_storage._get_session_path(self._tenant_id, session_id)
            full_path = session_path / "workspace" / relative_path
            return full_path.stat().st_size
        except Exception:
            return 0

    async def _generate_final_report(
        self,
        session_id: str,
        summary: str,
        key_findings: List[str],
        total_time: int,
    ) -> str:
        """Generate a consolidated report.md in the workspace from all session data."""
        try:
            state_content = await self.session_storage.read(self._tenant_id, session_id, "state.json")
            state = json.loads(state_content) if state_content else {}
        except Exception:
            state = {}

        plan = state.get("plan", {})
        todos = state.get("todos", [])
        sources = state.get("sources", [])
        connectors = state.get("connectors", [])
        original_message = state.get("original_message", "")

        # Build report markdown
        lines = []
        lines.append(f"# {plan.get('title', 'Deep Work Report')}")
        lines.append("")
        if original_message:
            lines.append(f"> {original_message}")
            lines.append("")
        lines.append(f"**Duration:** {total_time // 60}m {total_time % 60}s  ")
        lines.append(f"**Steps:** {self._tool_call_count}")
        lines.append("")

        if summary:
            lines.append("## Summary")
            lines.append("")
            lines.append(summary)
            lines.append("")

        if key_findings:
            lines.append("## Key Findings")
            lines.append("")
            for f in key_findings:
                lines.append(f"- {f}")
            lines.append("")

        if todos:
            lines.append("## Tasks")
            lines.append("")
            for t in todos:
                check = "x" if t.get("status") == "completed" else " "
                lines.append(f"- [{check}] {t.get('text', '')}")
            lines.append("")

        if sources:
            lines.append("## Sources")
            lines.append("")
            for s in sources:
                if s.get("url"):
                    lines.append(f"- [{s.get('title', s.get('domain', 'Link'))}]({s['url']})")
                else:
                    lines.append(f"- {s.get('title', 'Unknown source')}")
            lines.append("")

        if connectors:
            lines.append("## Apps & Connectors")
            lines.append("")
            for c in connectors:
                lines.append(f"- {c.get('name', 'Unknown')}")
            lines.append("")

        # Collect workspace file contents (only text files, skip report.md itself)
        workspace_files = await self._list_workspace_files(session_id)
        text_extensions = {".md", ".txt", ".json", ".csv", ".html", ".xml", ".yaml", ".yml"}
        file_contents: List[tuple] = []
        for fname in workspace_files:
            if fname == "report.md":
                continue
            ext = "." + fname.rsplit(".", 1)[-1] if "." in fname else ""
            if ext.lower() in text_extensions:
                try:
                    content = await self.session_storage.read(
                        self._tenant_id, session_id, f"workspace/{fname}"
                    )
                    if content and len(content) < 50000:
                        file_contents.append((fname, content))
                except Exception:
                    pass

        if file_contents:
            lines.append("## Artifacts")
            lines.append("")
            for fname, content in file_contents:
                lines.append(f"### {fname}")
                lines.append("")
                ext = fname.rsplit(".", 1)[-1] if "." in fname else ""
                lines.append(f"```{ext}")
                lines.append(content.rstrip())
                lines.append("```")
                lines.append("")

        report_content = "\n".join(lines)

        # Write to workspace
        session_path = self.session_storage._get_session_path(self._tenant_id, session_id)
        workspace = session_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        report_path = workspace / "report.md"
        report_path.write_text(report_content, encoding="utf-8")

        # Stream workspace update for the report file
        await self._stream_event("workspace_update", {
            "action": "create",
            "path": "report.md",
            "size": len(report_content),
        })

        return "report.md"

    async def _list_workspace_files(self, session_id: str) -> List[str]:
        """List files in the workspace directory."""
        try:
            session_path = self.session_storage._get_session_path(self._tenant_id, session_id)
            workspace = session_path / "workspace"
            if not workspace.exists():
                return []
            return [
                str(f.relative_to(workspace))
                for f in workspace.rglob("*")
                if f.is_file()
            ]
        except Exception:
            return []

    @staticmethod
    def _parse_todos(content: str) -> List[Dict[str, str]]:
        """Parse todo.md content into structured list."""
        todos = []
        for line in content.strip().split("\n"):
            line = line.strip()
            if line.startswith("- [x] "):
                todos.append({"text": line[6:], "status": "completed"})
            elif line.startswith("- [ ] "):
                todos.append({"text": line[6:], "status": "pending"})
            elif line.startswith("- [~] "):
                todos.append({"text": line[6:], "status": "in_progress"})
        return todos
