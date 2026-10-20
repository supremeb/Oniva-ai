"""Background Agent Executor — lightweight autonomous agent loop.

Runs a CustomAgent's LLM tool loop to completion, updating the BackgroundJob
with execution trace and progress at each step. Designed for scheduled and
webhook-triggered background runs (not interactive chat).

Usage:
    asyncio.create_task(run_background_agent(job_id, agent_id, tenant_id, user_id, input_data))

Pattern follows _run_deep_work() in deep_work.py: own DB session, re-set tenant
context after every commit, fresh session for error handling.
"""

import asyncio
import inspect
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, set_tenant_context
from app.models.background_job import BackgroundJob
from app.models.custom_agent import CustomAgent
from app.services.llm_gateway import LLMGateway, LLMRequest
from app.services.intervention import clear_interventions
from app.services.run_event_service import emit_run_event

logger = logging.getLogger(__name__)

# Pure prompt/tool/connector logic lives in fleet_engine_agent (phase 6 U6); re-exported so the
# legacy executor and its tests are unchanged.
from app.services.fleet_engine_agent import (  # noqa: E402,F401
    _BATCH_SUPERSEDES,
    _SESSION_DEPENDENT_SKILLS,
    DEFAULT_MAX_DURATION_SEC,
    DEFAULT_MAX_TOOL_CALLS,
    _build_system_prompt,
    _load_tools,
    _preload_connector_summary,
)

# Budget defaults live in fleet_engine_agent (re-exported above)
DEFAULT_MAX_ITERATIONS = 100  # safety cap on LLM loop iterations


async def run_background_agent(
    job_id: UUID,
    agent_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    input_data: Optional[dict] = None,
) -> None:
    """Execute a background agent job autonomously.

    Opens its own DB session, loads agent config, runs an iterative LLM tool
    loop, updates execution_trace in the BackgroundJob for Fleet SSE streaming,
    and delivers results via webhook on completion.

    Args:
        job_id: BackgroundJob UUID
        agent_id: CustomAgent UUID
        tenant_id: Tenant UUID for RLS context
        user_id: User UUID (agent owner)
        input_data: Optional input parameters / task description
    """
    async with AsyncSessionLocal() as db:
        await set_tenant_context(db, str(tenant_id))
        try:
            agent_name: Optional[str] = None  # initialized early for except-block access

            # ── Mark job as running ──────────────────────────────────────
            result = await db.execute(
                select(BackgroundJob).where(BackgroundJob.id == job_id)
            )
            job = result.scalar_one_or_none()
            if not job:
                logger.error(f"Background job {job_id} not found")
                return

            job.start()
            await db.commit()
            await set_tenant_context(db, str(tenant_id))

            # ── Load agent config ────────────────────────────────────────
            agent_result = await db.execute(
                select(CustomAgent).where(CustomAgent.id == agent_id)
            )
            agent = agent_result.scalar_one_or_none()
            if not agent:
                raise ValueError(f"Agent {agent_id} not found")

            system_prompt = agent.system_prompt or ""
            model = agent.model or "gpt-4o"
            temperature = agent.temperature or 0.7
            agent_skills = agent.skills or []
            resource_constraints = agent.resource_constraints or {}
            background_config = agent.background_config or {}
            agent_name = agent.name

            # Budget limits
            max_tool_calls = resource_constraints.get("max_tool_calls", DEFAULT_MAX_TOOL_CALLS)
            max_duration = resource_constraints.get("timeout_seconds", DEFAULT_MAX_DURATION_SEC)

            # ── Build initial messages ───────────────────────────────────
            task_description = ""
            if input_data:
                task_description = input_data.get("task", input_data.get("message", json.dumps(input_data)))
            if not task_description:
                task_description = "Execute your configured task."

            # ── Initialize services ─────────────────────────────────────
            llm_gateway = LLMGateway(db)
            tools = _load_tools(agent_skills)

            # Pre-resolve connector list so the agent skips the discovery call
            connector_summary = None
            has_connector_skills = any(
                s in agent_skills
                for s in ("list_available_connectors", "invoke_connector_tool")
            )
            if has_connector_skills:
                connector_summary = await _preload_connector_summary(
                    str(user_id), str(tenant_id)
                )

            messages: List[Dict[str, Any]] = [
                {"role": "system", "content": _build_system_prompt(
                    system_prompt, agent_skills, connector_summary
                )},
                {"role": "user", "content": task_description},
            ]

            # ── Emit run_started ─────────────────────────────────────────
            await emit_run_event(
                db,
                run_id=str(job_id),
                tenant_id=str(tenant_id),
                event_type="run_started",
                agent_name=agent_name,
            )

            # ── Agent loop ───────────────────────────────────────────────
            execution_trace: List[Dict[str, Any]] = []
            tool_call_count = 0
            no_tool_count = 0
            start_time = time.time()

            for iteration in range(1, DEFAULT_MAX_ITERATIONS + 1):
                elapsed = time.time() - start_time

                # Budget checks
                if tool_call_count >= max_tool_calls:
                    logger.info(f"Job {job_id}: max tool calls reached ({max_tool_calls})")
                    break
                if elapsed >= max_duration:
                    logger.info(f"Job {job_id}: timeout reached ({max_duration}s)")
                    break

                # Check interventions (operator messages from Fleet)
                interventions = await _safe_check_interventions(str(job_id))
                for intervention in interventions:
                    messages.append({
                        "role": "user",
                        "content": f"[Operator Intervention] {intervention.get('message', '')}",
                    })

                # Call LLM
                step_start = time.time()
                try:
                    llm_request = LLMRequest(
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        tools=_build_openai_tools(tools) if tools else None,
                        agent_id=agent_id,
                    )
                    response = await llm_gateway.complete(
                        llm_request,
                        tenant_id=tenant_id,
                        user_id=user_id,
                    )
                except Exception as e:
                    detail = str(e)
                    if hasattr(e, "errors"):
                        detail += " | " + "; ".join(e.errors)
                    logger.error(f"Job {job_id} LLM call failed at iteration {iteration}: {detail}")
                    raise RuntimeError(f"LLM call failed: {detail}")

                step_duration_ms = int((time.time() - step_start) * 1000)
                content = response.content
                tool_calls = response.tool_calls

                # ── Handle tool calls ────────────────────────────────────
                if tool_calls:
                    # Append assistant message with tool calls
                    assistant_msg: Dict[str, Any] = {"role": "assistant", "content": content or ""}
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                        }
                        for tc in tool_calls
                    ]
                    messages.append(assistant_msg)

                    for tc in tool_calls:
                        # Per-tool budget check — stop before exceeding limit
                        if tool_call_count >= max_tool_calls:
                            logger.info(f"Job {job_id}: tool budget exhausted mid-iteration ({max_tool_calls})")
                            break

                        tool_start = time.time()
                        tool_result = await _execute_tool(
                            tools, tc.name, tc.arguments, str(user_id), str(tenant_id)
                        )
                        tool_duration_ms = int((time.time() - tool_start) * 1000)
                        tool_call_count += 1

                        # Append tool result
                        result_str = json.dumps(tool_result) if isinstance(tool_result, dict) else str(tool_result)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_str,
                        })

                        # Record in trace
                        trace_entry = {
                            "step": iteration,
                            "type": "tool_call",
                            "tool_name": tc.name,
                            "tool_args": tc.arguments,
                            "tool_result_preview": result_str[:500],
                            "duration_ms": tool_duration_ms,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "token_usage": {
                                "input": response.input_tokens,
                                "output": response.output_tokens,
                                "total": response.input_tokens + response.output_tokens,
                            },
                        }
                        execution_trace.append(trace_entry)

                    no_tool_count = 0
                else:
                    # No tool calls — agent responded with text
                    no_tool_count += 1
                    messages.append({"role": "assistant", "content": content})

                    trace_entry = {
                        "step": iteration,
                        "type": "text_response",
                        "content_preview": content or "",
                        "duration_ms": step_duration_ms,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "token_usage": {
                            "input": response.input_tokens,
                            "output": response.output_tokens,
                            "total": response.input_tokens + response.output_tokens,
                        },
                    }
                    execution_trace.append(trace_entry)

                    # Agent responded with text and no tool calls — task is done
                    if no_tool_count >= 1:
                        logger.info(f"Job {job_id}: agent produced text response with no tool calls, finishing")
                        break

                # ── Update job progress in DB ────────────────────────────
                progress = min(95, int((iteration / DEFAULT_MAX_ITERATIONS) * 100))
                async with db.begin_nested():
                    result = await db.execute(
                        select(BackgroundJob).where(BackgroundJob.id == job_id)
                    )
                    job = result.scalar_one_or_none()
                    if job:
                        job.execution_trace = list(execution_trace)
                        job.progress = progress
                await db.commit()
                await set_tenant_context(db, str(tenant_id))

            # ── Mark job completed ───────────────────────────────────────
            elapsed_total = time.time() - start_time
            final_content = content if content else "Agent completed without final output."

            result = await db.execute(
                select(BackgroundJob).where(BackgroundJob.id == job_id)
            )
            job = result.scalar_one_or_none()
            if job:
                job.complete(result={
                    "output": final_content,
                    "tool_calls_made": tool_call_count,
                    "iterations": iteration,
                    "duration_seconds": round(elapsed_total, 1),
                })
                job.execution_trace = list(execution_trace)
                await db.commit()
                await set_tenant_context(db, str(tenant_id))

            # ── Emit run_completed ───────────────────────────────────────
            await emit_run_event(
                db,
                run_id=str(job_id),
                tenant_id=str(tenant_id),
                event_type="run_completed",
                agent_name=agent_name,
                payload={
                    "tool_calls_made": tool_call_count,
                    "iterations": iteration,
                    "duration_seconds": round(elapsed_total, 1),
                },
            )

            # ── Deliver via webhook ──────────────────────────────────────
            await _safe_deliver(
                job_id=job_id,
                agent_id=agent_id,
                agent_name=agent_name,
                result_text=final_content,
                input_prompt=task_description,
                background_config=background_config,
                db=db,
                tenant_id=tenant_id,
            )

            logger.info(
                f"Background agent job {job_id} completed: "
                f"{tool_call_count} tool calls, {iteration} iterations, "
                f"{elapsed_total:.1f}s elapsed"
            )

        except Exception as e:
            logger.exception(f"Background agent job {job_id} failed: {e}")
            # Use a fresh session to mark failure (original may be broken)
            try:
                async with AsyncSessionLocal() as fail_db:
                    await set_tenant_context(fail_db, str(tenant_id))
                    result = await fail_db.execute(
                        select(BackgroundJob).where(BackgroundJob.id == job_id)
                    )
                    job = result.scalar_one_or_none()
                    if job:
                        job.fail(str(e)[:2000])
                    # Emit run_failed alongside job failure mark
                    await emit_run_event(
                        fail_db,
                        run_id=str(job_id),
                        tenant_id=str(tenant_id),
                        event_type="run_failed",
                        agent_name=agent_name,
                        payload={"error": str(e)[:2000]},
                    )
                    await fail_db.commit()
            except Exception as inner_e:
                logger.error(f"Failed to mark job {job_id} as failed: {inner_e}")

            # Still attempt webhook delivery on failure
            try:
                async with AsyncSessionLocal() as webhook_db:
                    await set_tenant_context(webhook_db, str(tenant_id))
                    agent_result = await webhook_db.execute(
                        select(CustomAgent).where(CustomAgent.id == agent_id)
                    )
                    agent = agent_result.scalar_one_or_none()
                    if agent and agent.background_config:
                        await _safe_deliver(
                            job_id=job_id,
                            agent_id=agent_id,
                            agent_name=agent.name if agent else "Unknown",
                            result_text=f"Agent execution failed: {e}",
                            input_prompt=input_data.get("task", "") if input_data else "",
                            background_config=agent.background_config,
                            db=webhook_db,
                            tenant_id=tenant_id,
                        )
            except Exception:
                logger.error(f"Failed to deliver webhook for failed job {job_id}")
        finally:
            # Clear any remaining interventions
            try:
                await clear_interventions(str(job_id))
            except Exception:
                pass


# ─── Helper functions ────────────────────────────────────────────────────────


def _build_openai_tools(tools: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build OpenAI-format tool definitions for the LLM request."""
    openai_tools = []
    for name, skill in tools.items():
        # Extract parameters from handler signature
        params = {}
        required = []
        try:
            sig = inspect.signature(skill.handler)
            for pname, param in sig.parameters.items():
                if pname in ("ctx", "self", "args", "kwargs"):
                    continue
                param_type = "string"  # default
                annotation = param.annotation
                if annotation != inspect.Parameter.empty:
                    if annotation == int:
                        param_type = "integer"
                    elif annotation == float:
                        param_type = "number"
                    elif annotation == bool:
                        param_type = "boolean"
                    elif annotation == list or annotation == List:
                        param_type = "array"
                    elif annotation == dict or annotation == Dict:
                        param_type = "object"
                params[pname] = {"type": param_type}
                if param.default == inspect.Parameter.empty:
                    required.append(pname)
        except (ValueError, TypeError):
            pass

        tool_def = {
            "type": "function",
            "function": {
                "name": name,
                "description": skill.description or f"Tool: {name}",
                "parameters": {
                    "type": "object",
                    "properties": params,
                    "required": required,
                },
            },
        }
        openai_tools.append(tool_def)
    return openai_tools


async def _execute_tool(
    tools: Dict[str, Any],
    tool_name: str,
    tool_args: Dict[str, Any],
    user_id: str,
    tenant_id: str,
) -> Any:
    """Execute a tool by name with the given arguments."""
    skill = tools.get(tool_name)
    if not skill or not skill.handler:
        return {"success": False, "error": f"Unknown tool: {tool_name}"}

    try:
        from app.agents.skills.context import SkillContext

        # Deserialize any nested JSON string arguments (LLM may serialize dicts as strings)
        for k, v in list(tool_args.items()):
            if isinstance(v, str):
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, (dict, list)):
                        tool_args[k] = parsed
                except (json.JSONDecodeError, ValueError):
                    pass

        ctx = SkillContext(
            user_id=user_id,
            tenant_id=tenant_id,
        )
        result = await skill.handler(ctx, **tool_args)
        return result
    except Exception as e:
        logger.error(f"Tool {tool_name} failed: {e}")
        return {"success": False, "error": str(e)}


async def _safe_check_interventions(job_id: str) -> list:
    """Check for operator interventions, swallowing Redis errors."""
    try:
        from app.services.intervention import check_interventions
        return await check_interventions(job_id)
    except Exception as e:
        logger.debug(f"Intervention check failed (non-critical): {e}")
        return []


async def _safe_deliver(
    job_id: UUID,
    agent_id: UUID,
    agent_name: str,
    result_text: str,
    input_prompt: str,
    background_config: dict,
    db: Any,
    tenant_id: UUID,
) -> None:
    """Deliver results via output gating, swallowing errors."""
    try:
        from app.services.output_gating import evaluate_and_deliver
        await evaluate_and_deliver(
            job_id=job_id,
            agent_id=agent_id,
            agent_name=agent_name,
            result_text=result_text,
            input_prompt=input_prompt,
            background_config=background_config,
            db=db,
            tenant_id=tenant_id,
        )
    except Exception as e:
        logger.error(f"Webhook delivery failed for job {job_id}: {e}")
