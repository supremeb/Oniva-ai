"""Deep Work Orchestrator-Worker-Synthesizer Engine."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.agents.skills.registry import SkillRegistry
from app.services.deep_work.models import (
    DeepWorkPlan,
    IntakeResult,
    Learning,
    ReflectionResult,
    ReportOutline,
    ReportSection,
    WorkerResult,
    WorkerTask,
)
from app.services.deep_work.event_sink import EventSink, SSEQueueSink, NullSink
from app.services.llm_gateway import LLMGateway, LLMRequest, resolve_model_for_tier, resolve_model_for_policy, resolve_all_llm_keys
from app.services.deep_work.synthesis import build_sources_section, select_learnings as _select_learnings
from app.services.session_storage import SessionStorage
from app.services.run_event_service import emit_run_event

logger = logging.getLogger(__name__)

async def _build_control_plane_env_vars(
    tenant_id: str,
    user_id: str,
    session_id: str,
    worker_id: str,
    proxy_url: str,
) -> tuple[str, dict[str, str]]:
    """Build sandbox env vars using control plane proxy (zero-secrets).

    The ONLY way deep-work gives a sandbox LLM access. Fails closed when no
    proxy URL is configured. Returns (token, env_vars).
    """
    from app.services.sandbox_tokens import create_sandbox_token

    if not proxy_url:
        raise RuntimeError(
            "SANDBOX_PROXY_URL is not set; refusing to start a sandbox without the "
            "control-plane proxy (real provider keys are never injected)."
        )

    token = await create_sandbox_token(tenant_id, user_id, session_id, worker_id)
    env_vars = {
        "OPENAI_API_KEY": token,
        "OPENAI_BASE_URL": f"{proxy_url}/api/v1/sandbox-proxy/v1",
        "SESSION_ID": session_id,
    }
    return token, env_vars


class DeepWorkEngine:
    """Orchestrator-Worker-Synthesizer pipeline for deep work."""

    def __init__(
        self,
        db: Any,
        session_storage: SessionStorage,
        tenant_id: str,
        user_id: str,
        session_id: str,
        sse_queue: asyncio.Queue | None = None,
        agent_config: dict | None = None,
        event_sink: EventSink | None = None,
    ):
        self._db = db
        self._gateway = LLMGateway(db)
        self._session_storage = session_storage
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._session_id = session_id
        self._sse_queue = sse_queue  # kept for backward compat; engine uses _event_sink
        self._event_sink: EventSink = event_sink or (SSEQueueSink(sse_queue) if sse_queue else NullSink())
        self._agent_config = agent_config
        self._semaphore = asyncio.Semaphore(8)
        self._completed_workers: list[dict] = []
        self._accumulated_sources: list[dict] = []
        self._artifacts: list[dict] = []
        self._current_phase: str = "researching"

    async def _resolve_models_and_keys(self) -> tuple[dict[str, str], dict[str, str]]:
        """Resolve LLM model tiers and API keys fresh on each call.

        Returns (models, api_keys) where models is a dict with 'high', 'mid',
        'low' tier keys and api_keys is a dict of provider -> key.
        Raises RuntimeError if model resolution fails.
        """
        import os
        tenant_uuid = UUID(self._tenant_id)

        # Resolve model tiers
        models: dict[str, str] = {}
        for tier in ("high", "mid", "low"):
            models[tier] = await resolve_model_for_tier(
                self._db, tenant_uuid, tier, self._agent_config
            )
        logger.info(
            "Resolved models: high=%s mid=%s low=%s",
            models["high"], models["mid"], models["low"],
        )

        # Resolve LLM provider keys (tenant-level)
        keys = await resolve_all_llm_keys(self._db, tenant_uuid)
        settings_obj = get_settings()
        if "openai" not in keys and settings_obj.OPENAI_API_KEY:
            keys["openai"] = settings_obj.OPENAI_API_KEY
        if "anthropic" not in keys and settings_obj.ANTHROPIC_API_KEY:
            keys["anthropic"] = settings_obj.ANTHROPIC_API_KEY
        if "openrouter" not in keys and settings_obj.OPENROUTER_API_KEY:
            keys["openrouter"] = settings_obj.OPENROUTER_API_KEY

        # Load search provider keys from api_keys table (user-level tool keys)
        search_providers = ("tavily", "parallelai", "exa", "serpapi")
        try:
            from sqlalchemy import select
            from app.models.api_key import APIKey
            from app.core.encryption import get_encryption_service
            search_keys_result = await self._db.execute(
                select(APIKey).where(
                    APIKey.tenant_id == tenant_uuid,
                    APIKey.is_active == True,  # noqa: E712
                    APIKey.provider.in_(search_providers),
                )
            )
            for api_key_row in search_keys_result.scalars():
                if api_key_row.provider not in keys:
                    decrypted = get_encryption_service().decrypt(api_key_row.encrypted_key)
                    keys[api_key_row.provider] = decrypted
        except Exception:
            logger.exception("Failed to load search keys from api_keys table")

        # Fallback to env vars for search providers
        env_fallbacks = {
            "tavily": "TAVILY_API_KEY",
            "parallelai": "PARALLEL_API_KEY",
            "exa": "EXA_API_KEY",
            "serpapi": "SERP_API_KEY",
        }
        for provider, env_var in env_fallbacks.items():
            if provider not in keys:
                env_val = os.environ.get(env_var, "")
                if env_val:
                    keys[provider] = env_val

        return models, keys

    async def run(
        self,
        query: str,
        precomputed_plan: DeepWorkPlan | None = None,
        enriched_query: str | None = None,
    ) -> str:
        """Execute full deep work pipeline. Returns report markdown."""
        settings = get_settings()
        timeout = settings.DEEP_WORK_MAX_DURATION_SEC
        try:
            return await asyncio.wait_for(
                self._run_pipeline(enriched_query or query, precomputed_plan),
                timeout=timeout,
            )
        except Exception as _pipeline_exc:
            # Emit run_failed (fire-and-forget) before re-raising
            await emit_run_event(
                self._db,
                run_id=self._session_id,
                tenant_id=self._tenant_id,
                event_type="run_failed",
                session_id=self._session_id,
                agent_name="deep_work_engine",
                payload={"error": str(_pipeline_exc)},
            )
            raise

    async def plan_only(self, query: str, enriched_query: str | None = None) -> DeepWorkPlan:
        """Run orchestration only and return the plan (no workers/synthesis)."""
        models, _ = await self._resolve_models_and_keys()
        return await self._orchestrate(enriched_query or query, models)

    async def generate_intake_questions(
        self,
        query: str,
        available_kbs: list[str] | None = None,
        available_connectors: list[str] | None = None,
    ) -> IntakeResult:
        """Ask clarifier LLM whether to ask questions or auto-proceed."""
        models, _ = await self._resolve_models_and_keys()
        tenant_uuid = UUID(self._tenant_id)
        prompt = _load_prompt("intake.md")
        user_msg = self._format_intake_context(
            query, available_kbs or [], available_connectors or [],
        )
        try:
            response = await self._gateway.complete(
                LLMRequest(
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    model=models["mid"],
                    temperature=0.2,
                    max_tokens=1000,
                ),
                tenant_id=tenant_uuid,
                user_id=UUID(self._user_id),
            )
            result = _parse_json_response(response.content, IntakeResult)
            logger.info(
                "Intake clarifier result: action=%s questions=%d",
                result.action,
                len(result.questions) if result.questions else 0,
            )
            return result
        except Exception:
            logger.exception("Intake clarifier failed, auto-proceeding")
            return IntakeResult(action="proceed", enriched_query=query)

    async def _run_pipeline(
        self, query: str, precomputed_plan: DeepWorkPlan | None = None,
    ) -> str:
        settings = get_settings()
        max_reflection_rounds = settings.DEEP_WORK_MAX_REFLECTION_ROUNDS

        # 0. Resolve model tiers and API keys fresh at pipeline start
        models, api_keys = await self._resolve_models_and_keys()

        # Emit run_started (fire-and-forget)
        await emit_run_event(
            self._db,
            run_id=self._session_id,
            tenant_id=self._tenant_id,
            event_type="run_started",
            session_id=self._session_id,
            agent_name="deep_work_engine",
        )

        search_providers = ("tavily", "parallelai", "exa", "serpapi")
        search_available = any(k in api_keys for k in search_providers)
        if not search_available:
            logger.warning("No search API key found — web_search will fall back to mock")

        # 0c. Build dynamic tool list based on what's actually configured
        available_tools = ["browse_url", "read_file"]
        if any(k in api_keys for k in search_providers):
            available_tools.insert(0, "web_search")
        if bool(settings.ENABLE_SANDBOX):
            available_tools.extend(["browser_navigate", "browser_screenshot", "shell_exec"])
        # search_knowledge always listed — worker will no-op if no KBs exist
        available_tools.append("search_knowledge")
        self._available_tools = available_tools

        # 1. Orchestrate (or use precomputed plan)
        if precomputed_plan is not None:
            plan = precomputed_plan
        else:
            plan = await self._orchestrate(query, models)
        await self._session_storage.write(
            self._tenant_id, self._session_id, "engine_plan.json",
            plan.model_dump_json(),
        )
        await self._emit("deep_work_engine_planned", {
            "sub_queries": [t.sub_query for t in plan.sub_queries],
        })

        # 2. Workers + reflection loop
        all_learnings: list[Learning] = []
        tasks_to_run = plan.sub_queries
        await self._set_phase("researching")

        # Emit phase_transition: workers (fire-and-forget)
        await emit_run_event(
            self._db,
            run_id=self._session_id,
            tenant_id=self._tenant_id,
            event_type="phase_transition",
            session_id=self._session_id,
            agent_name="deep_work_engine",
            payload={"phase": "workers"},
        )

        for round_num in range(max_reflection_rounds + 1):
            results = await self._run_workers(tasks_to_run, models, api_keys)
            all_learnings.extend(self._collect_learnings(results))

            # Reflect (not on final round)
            if round_num < max_reflection_rounds:
                await self._set_phase("reflecting")
                reflection = await self._reflect(query, plan, results, models)
                await self._emit("deep_work_engine_reflection", {
                    "score": reflection.score,
                    "gaps": reflection.gaps,
                    "round": round_num,
                    "continuing": reflection.should_continue,
                })
                await self._persist_engine_state(reflection={
                    "score": reflection.score,
                    "gaps": reflection.gaps,
                    "round": round_num,
                })
                if reflection.should_continue and reflection.gap_filling_tasks:
                    # Emit ordered gap-filling tasks so frontend can batch-append
                    await self._emit("deep_work_engine_gap_tasks", {
                        "sub_queries": [
                            {"worker_id": t.worker_id, "sub_query": t.sub_query}
                            for t in reflection.gap_filling_tasks
                        ],
                        "round": round_num,
                    })
                    # Persist updated plan so refresh recovery includes gap tasks
                    plan.sub_queries.extend(reflection.gap_filling_tasks)
                    await self._session_storage.write(
                        self._tenant_id, self._session_id, "engine_plan.json",
                        plan.model_dump_json(),
                    )
                    await self._set_phase("researching")
                    tasks_to_run = reflection.gap_filling_tasks
                    continue
            break

        # 3. Synthesize (streaming)
        await self._set_phase("synthesizing")
        # Emit phase_transition: synthesize (fire-and-forget)
        await emit_run_event(
            self._db,
            run_id=self._session_id,
            tenant_id=self._tenant_id,
            event_type="phase_transition",
            session_id=self._session_id,
            agent_name="deep_work_engine",
            payload={"phase": "synthesize"},
        )
        report = await self._synthesize(query, all_learnings, plan, models)

        # 4. Output phase (if deliverable requested)
        if plan.deliverable:
            try:
                await self._generate_output(plan, report, all_learnings, api_keys, models)
            except Exception:
                logger.exception("Output phase failed, delivering markdown only")

        # 5. Persist final results
        await self._persist_results(plan, all_learnings, report)
        await self._set_phase("completed")
        await self._emit("deep_work_engine_complete", {
            "report_length": len(report),
        })
        # Emit run_completed (fire-and-forget)
        await emit_run_event(
            self._db,
            run_id=self._session_id,
            tenant_id=self._tenant_id,
            event_type="run_completed",
            session_id=self._session_id,
            agent_name="deep_work_engine",
            payload={"report_length": len(report)},
        )
        return report

    # ── Workers ─────────────────────────────────────────────────

    async def _run_workers(
        self,
        tasks: list[WorkerTask],
        models: dict[str, str],
        api_keys: dict[str, str],
    ) -> list[WorkerResult]:
        from app.services.deep_work.worker import DeepWorkWorker
        from app.services.deep_work.sandbox_tools import get_sandbox_tools

        # Check if E2B sandbox is available (env key or user DB key)
        settings = get_settings()
        e2b_key = settings.E2B_API_KEY or None
        if settings.ENABLE_SANDBOX and not e2b_key:
            try:
                from app.services.sandbox_executor import resolve_e2b_key
                e2b_key = await resolve_e2b_key(self._tenant_id, self._user_id)
            except Exception:
                pass

        sandbox_available = bool(settings.ENABLE_SANDBOX and e2b_key)
        executor = None
        if sandbox_available:
            from app.services.sandbox_executor import get_sandbox_executor
            executor = get_sandbox_executor()

        async def _run_one(task: WorkerTask) -> WorkerResult:
            import time as _time
            worker_start = _time.monotonic()
            sandbox = None
            try:
                # Eager sandbox creation per worker
                if executor:
                    try:
                        # Zero-secrets: proxy token only, never real keys
                        sandbox_token, sandbox_env = await _build_control_plane_env_vars(
                            self._tenant_id, self._user_id,
                            self._session_id, task.worker_id,
                            get_settings().SANDBOX_PROXY_URL,
                        )
                        sandbox = await executor.get_or_create(
                            session_id=self._session_id,
                            worker_id=task.worker_id,
                            api_key=e2b_key,
                            env_vars=sandbox_env,
                        )
                        # Store token on handle for revocation at teardown
                        if sandbox_token:
                            sandbox.token = sandbox_token
                    except Exception as exc:
                        logger.warning(
                            "Sandbox creation failed for worker %s, continuing without: %s",
                            task.worker_id, exc,
                        )

                # Resolve model per-worker via routing policies
                try:
                    worker_model = await resolve_model_for_policy(
                        self._db, UUID(self._tenant_id),
                        task.task_type, task.output_type, self._agent_config,
                    )
                except Exception:
                    worker_model = models["mid"]

                worker = DeepWorkWorker(
                    self._gateway, self._tenant_id, self._user_id,
                    self._session_storage, self._session_id,
                    worker_model=worker_model,
                    extractor_model=models["low"],
                    api_semaphore=self._semaphore,
                    api_keys=api_keys,
                    sandbox=sandbox,
                )
                await self._emit("deep_work_engine_worker_started", {
                    "worker_id": task.worker_id, "sub_query": task.sub_query,
                })
                try:
                    tools_schema = SkillRegistry.get_tools_as_openai_functions(task.tools)
                    # Add sandbox tools (shell_exec + browser tools if enabled)
                    if sandbox:
                        tools_schema = tools_schema + get_sandbox_tools()
                    result = await worker.research(task, tools_schema, self._event_sink)
                except Exception as exc:
                    logger.exception("Worker %s failed: %s", task.worker_id, exc)
                    result = WorkerResult(
                        worker_id=task.worker_id, sub_query=task.sub_query,
                        learnings=[], sources=[], confidence=0.0,
                        iterations_used=0, error=str(exc),
                    )
                # Emit sources discovered by this worker
                source_items = [
                    {
                        "type": "url" if l.source_type == "web" else l.source_type,
                        "title": (
                            getattr(l, "source_title", None)
                            or l.fact.split(". ")[0][:120]
                        ),
                        "url": l.source_url,
                    }
                    for l in result.learnings
                    if l.source_url
                ]
                if source_items:
                    await self._emit("source_update", {
                        "sources": source_items,
                        "connectors": [],
                    })

                # Track completed worker and accumulate sources for refresh recovery
                duration_seconds = round(_time.monotonic() - worker_start, 1)
                top_findings = [
                    l.fact[:200] for l in result.learnings[:3]
                ]
                self._completed_workers.append({
                    "worker_id": task.worker_id,
                    "sub_query": task.sub_query,
                    "status": "error" if result.error else "complete",
                    "learning_count": len(result.learnings),
                    "source_count": len(result.sources),
                    "duration_seconds": duration_seconds,
                })
                self._accumulated_sources.extend(source_items)
                await self._persist_engine_state()
                await self._emit("deep_work_engine_worker_complete", {
                    "worker_id": task.worker_id,
                    "learning_count": len(result.learnings),
                    "tool_call_count": result.iterations_used,
                    "source_count": len(result.sources),
                    "top_findings": top_findings,
                    "duration_seconds": duration_seconds,
                    "error": result.error,
                })
                return result
            finally:
                # Download workspace artifacts before teardown
                if sandbox and executor:
                    try:
                        artifacts = await sandbox.download_workspace()
                        for path, content in artifacts.items():
                            await self._session_storage.write_binary(
                                self._tenant_id,
                                self._session_id,
                                f"workers/{task.worker_id}/artifacts/{path}",
                                content,
                            )
                    except Exception:
                        logger.exception(
                            "Failed to download artifacts for worker %s",
                            task.worker_id,
                        )
                    await executor.teardown(self._session_id, task.worker_id)

        # return_exceptions=True so one failed worker doesn't crash all others
        results = await asyncio.gather(
            *[_run_one(t) for t in tasks],
            return_exceptions=True,
        )
        successful = [r for r in results if not isinstance(r, BaseException)]
        failed = [r for r in results if isinstance(r, BaseException)]
        if failed:
            logger.warning(
                "%d/%d workers failed: %s",
                len(failed), len(results),
                [str(e) for e in failed],
            )
        return list(successful)

    # ── Orchestrate ─────────────────────────────────────────────

    async def _orchestrate(self, query: str, models: dict[str, str]) -> DeepWorkPlan:
        prompt = _load_prompt("orchestrator.md")
        response = await self._gateway.complete(
            LLMRequest(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": self._build_orchestrator_msg(query)},
                ],
                model=models["high"],
                temperature=0.3,
                max_tokens=4000,
            ),
            tenant_id=UUID(self._tenant_id),
            user_id=UUID(self._user_id),
        )
        return _parse_json_response(response.content, DeepWorkPlan)

    def _build_orchestrator_msg(self, query: str) -> str:
        from datetime import date
        today = date.today().strftime("%B %d, %Y")
        tools_list = getattr(self, '_available_tools', None)
        if not tools_list:
            tools_list = ["web_search", "browse_url", "read_file", "search_knowledge"]
        return (
            f"Decompose this query into parallel research sub-queries.\n\n"
            f"Today's date: {today}\n\n"
            f"User Query: {query}\n\n"
            f"Available tools: {', '.join(tools_list)}"
        )

    # ── Reflect ─────────────────────────────────────────────────

    async def _reflect(
        self, query: str, plan: DeepWorkPlan, results: list[WorkerResult],
        models: dict[str, str],
    ) -> ReflectionResult:
        prompt = _load_prompt("reflection.md")
        response = await self._gateway.complete(
            LLMRequest(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": (
                        f"Query: {query}\n\n"
                        f"Learnings:\n{self._format_learnings(results)}"
                    )},
                ],
                model=models["mid"],
                temperature=0.2,
            ),
            tenant_id=UUID(self._tenant_id),
            user_id=UUID(self._user_id),
        )
        return _parse_json_response(response.content, ReflectionResult)

    def _format_learnings(self, results: list[WorkerResult]) -> str:
        lines = []
        for r in results:
            lines.append(f"## Worker {r.worker_id}: {r.sub_query}")
            if r.error:
                lines.append(f"  ERROR: {r.error}")
            for l in r.learnings:
                lines.append(f"  - [{l.confidence:.0%}] {l.fact}")
        return "\n".join(lines)

    # ── Synthesize ──────────────────────────────────────────────

    async def _synthesize(
        self, query: str, learnings: list[Learning], plan: DeepWorkPlan,
        models: dict[str, str],
    ) -> str:
        prompt_outliner = _load_prompt("outliner.md")
        prompt_section = _load_prompt("section_writer.md")
        selected = _select_learnings(learnings, max_tokens=20_000)

        # Pass 1: Generate outline (with retry)
        await self._emit("deep_work_engine_worker_activity", {
            "worker_id": "synthesizer",
            "activity_type": "synthesis",
            "description": "Planning report structure...",
        })
        outline = None
        last_err = None
        for attempt in range(3):
            try:
                outline_response = await self._gateway.complete(
                    LLMRequest(
                        messages=[
                            {"role": "system", "content": prompt_outliner},
                            {"role": "user", "content": self._build_synthesis_context(
                                query, selected, plan,
                            )},
                        ],
                        model=models["high"],
                        max_tokens=2000,
                    ),
                    tenant_id=UUID(self._tenant_id),
                    user_id=UUID(self._user_id),
                )
                outline = _parse_json_response(outline_response.content, ReportOutline)
                break
            except Exception as e:
                last_err = e
                logger.warning("Outline generation attempt %d failed: %s", attempt + 1, e)
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
        if outline is None:
            raise last_err or RuntimeError("Outline generation failed")

        # Pass 2: Write sections SEQUENTIALLY, streaming each one
        report_parts: list[str] = [f"# {outline.title}\n"]

        for idx, section in enumerate(outline.sections):
            section_learnings = [
                selected[i - 1] for i in section.learning_ids
                if 0 < i <= len(selected)
            ]
            await self._emit("deep_work_engine_worker_activity", {
                "worker_id": "synthesizer",
                "activity_type": "synthesis",
                "description": f"Writing section {idx + 1}/{len(outline.sections)}: {section.heading}...",
            })

            # Stream the section heading
            heading_md = f"\n## {section.heading}\n\n"
            await self._emit("deep_work_engine_synthesis_chunk", {
                "content": heading_md,
            })

            # Stream section body
            all_headings = [s.heading for s in outline.sections]
            section_chunks: list[str] = []
            try:
                async for chunk in self._gateway.stream(
                    LLMRequest(
                        messages=[
                            {"role": "system", "content": prompt_section},
                            {"role": "user", "content": self._build_section_context(
                                section, section_learnings, query, all_headings,
                            )},
                        ],
                        model=models["high"],
                        max_tokens=4000,
                    ),
                    tenant_id=UUID(self._tenant_id),
                    user_id=UUID(self._user_id),
                ):
                    section_chunks.append(chunk.content)
                    await self._emit("deep_work_engine_synthesis_chunk", {
                        "content": chunk.content,
                    })
                report_parts.append(heading_md + "".join(section_chunks))
            except Exception as exc:
                logger.warning("Section '%s' failed: %s", section.heading, exc)
                fallback = "[This section could not be generated.]\n"
                await self._emit("deep_work_engine_synthesis_chunk", {
                    "content": fallback,
                })
                report_parts.append(heading_md + fallback)

        # Pass 3: Generate executive summary + key takeaways (streamed)
        body_text = "\n".join(report_parts)
        try:
            closing = await self._write_closing(query, body_text, selected, models)
            report_parts.insert(1, closing["executive_summary"])  # after title
            report_parts.append(closing["key_takeaways"])
        except Exception as exc:
            logger.warning("Closing generation failed: %s", exc)
            report_parts.insert(1, "\n## Executive Summary\n\n[Summary could not be generated.]\n")
            report_parts.append("\n## Key Takeaways\n\n[Takeaways could not be generated.]\n")

        # Sources (deterministic, no LLM call)
        sources_md = self._build_sources_section(selected)
        await self._emit("deep_work_engine_synthesis_chunk", {
            "content": sources_md,
        })
        report_parts.append(sources_md)

        return "\n".join(report_parts)

    def _build_synthesis_context(
        self, query: str, learnings: list[Learning], plan: DeepWorkPlan,
    ) -> str:
        sections = [f"# Query\n{query}\n"]
        sections.append("# Research Plan")
        for t in plan.sub_queries:
            sections.append(f"- {t.sub_query}")
        sections.append("\n# Learnings")
        for i, l in enumerate(learnings, 1):
            sections.append(
                f"{i}. [{l.confidence:.0%}] {l.fact}\n"
                f"   Source: {l.source_url}"
            )
            if l.quote:
                sections.append(f'   Quote: "{l.quote}"')
        return "\n".join(sections)

    def _build_section_context(
        self, section: ReportSection, learnings: list[Learning], query: str,
        all_headings: list[str] | None = None,
    ) -> str:
        """Build context for a single section writer."""
        parts = [
            f"## Research Query\n{query}",
            f"\n## Section: {section.heading}",
            f"Description: {section.description}",
        ]
        if all_headings:
            parts.append(f"\n## Report Outline\n" + "\n".join(
                f"- {h}" for h in all_headings
            ))
        parts.append(
            f"\n## Assigned Learnings ({len(learnings)} learnings — "
            f"write depth proportional to evidence)"
        )
        for i, l in enumerate(learnings, 1):
            parts.append(
                f"{i}. [{l.confidence:.0%}] {l.fact}\n"
                f"   Source: {l.source_url}"
            )
            if l.quote:
                parts.append(f'   Quote: "{l.quote}"')
        return "\n".join(parts)

    async def _write_closing(
        self, query: str, body_text: str, learnings: list[Learning],
        models: dict[str, str],
    ) -> dict[str, str]:
        """Generate executive summary and key takeaways from completed sections."""
        prompt = (
            "Given a research report body and original query, write two sections:\n\n"
            "1. An Executive Summary (3-5 sentences covering what was researched and "
            "the top findings). Start with '## Executive Summary'.\n\n"
            "2. Key Takeaways (5-7 numbered items, each with an inline citation [N]). "
            "Include specific numbers, data points, or metrics in each takeaway. "
            "Start with '## Key Takeaways'.\n\n"
            "Write only these two sections, nothing else."
        )
        # Truncate body to fit context
        body_truncated = body_text[:30000]

        await self._emit("deep_work_engine_worker_activity", {
            "worker_id": "synthesizer",
            "activity_type": "synthesis",
            "description": "Writing executive summary and takeaways...",
        })

        chunks: list[str] = []
        async for chunk in self._gateway.stream(
            LLMRequest(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": (
                        f"Query: {query}\n\n"
                        f"Report body:\n{body_truncated}"
                    )},
                ],
                model=models["high"],
                max_tokens=2500,
            ),
            tenant_id=UUID(self._tenant_id),
            user_id=UUID(self._user_id),
        ):
            chunks.append(chunk.content)
            await self._emit("deep_work_engine_synthesis_chunk", {
                "content": chunk.content,
            })

        closing_text = "".join(chunks)

        # Split into executive summary and key takeaways
        if "## Key Takeaways" in closing_text:
            parts = closing_text.split("## Key Takeaways", 1)
            return {
                "executive_summary": parts[0].strip() + "\n",
                "key_takeaways": "\n## Key Takeaways" + parts[1],
            }
        return {
            "executive_summary": closing_text,
            "key_takeaways": "",
        }

    def _build_sources_section(self, learnings: list[Learning]) -> str:
        """Build deterministic sources section from learnings."""
        return build_sources_section(learnings)

    # ── Output Phase ─────────────────────────────────────────────

    async def _generate_output(
        self, plan: DeepWorkPlan, report_md: str, learnings: list[Learning],
        api_keys: dict[str, str], models: dict[str, str],
    ) -> None:
        """Generate deliverable file from synthesis using CodeAct in a sandbox."""
        if not plan.deliverable:
            return

        from app.services.deep_work.sandbox_tools import (
            get_sandbox_tools, execute_sandbox_tool, SANDBOX_TOOL_NAMES,
        )

        await self._set_phase("generating_output")
        await self._emit("deep_work_engine_output_started", {
            "deliverable_type": plan.deliverable,
            "deliverable_description": plan.deliverable_description,
        })

        # Resolve E2B key
        settings = get_settings()
        e2b_key = settings.E2B_API_KEY or None
        if settings.ENABLE_SANDBOX and not e2b_key:
            try:
                from app.services.sandbox_executor import resolve_e2b_key
                e2b_key = await resolve_e2b_key(self._tenant_id, self._user_id)
            except Exception:
                pass

        if not e2b_key or not settings.ENABLE_SANDBOX:
            logger.warning("Output phase skipped — sandbox not available")
            await self._emit("deep_work_engine_output_complete", {
                "artifact_uri": None, "download_url": None, "preview_url": None,
            })
            return

        from app.services.sandbox_executor import get_sandbox_executor
        executor = get_sandbox_executor()
        sandbox_token, sandbox_env = await _build_control_plane_env_vars(
            self._tenant_id, self._user_id, self._session_id, "output_agent",
            settings.SANDBOX_PROXY_URL,
        )

        sandbox = await executor.get_or_create(
            session_id=self._session_id,
            worker_id="output_agent",
            api_key=e2b_key,
            env_vars=sandbox_env,
        )
        sandbox.token = sandbox_token  # revoked on teardown

        try:
            # Load output generator prompt
            prompt = _load_prompt("output_generator.md")

            # Build user message with report + deliverable context
            selected_learnings = _select_learnings(learnings, max_tokens=8000)
            learnings_text = "\n".join(
                f"- [{l.confidence:.0%}] {l.fact}" for l in selected_learnings[:20]
            )
            user_msg = (
                f"## Deliverable Type\n{plan.deliverable}\n\n"
                f"## Description\n{plan.deliverable_description or 'Generate based on the report content.'}\n\n"
                f"## Research Report\n{report_md[:20000]}\n\n"
                f"## Key Data Points\n{learnings_text}"
            )

            # Output tools: shell_exec + inspect_artifact + publish_artifact
            tools_schema = get_sandbox_tools(include_browser=True, include_output=True)

            artifact_ctx = {
                "tenant_id": self._tenant_id,
                "session_id": self._session_id,
                "user_id": self._user_id,
            }

            # Run agent loop (max 10 iterations for generate-inspect-fix-publish)
            messages: list[dict] = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_msg},
            ]

            max_iterations = 10
            published = False
            logger.info(
                "Output agent starting: model=%s deliverable=%s",
                models["high"], plan.deliverable,
            )

            for iteration in range(max_iterations):
                await self._emit("deep_work_engine_output_progress", {
                    "iteration": iteration + 1,
                    "action": "thinking",
                    "description": f"Output agent iteration {iteration + 1}",
                })

                response = await self._gateway.complete(
                    LLMRequest(
                        messages=messages,
                        model=models["high"],
                        temperature=0.2,
                        max_tokens=16000,
                        tools=tools_schema,
                    ),
                    tenant_id=UUID(self._tenant_id),
                    user_id=UUID(self._user_id),
                )

                # Process tool calls
                tool_calls = getattr(response, "tool_calls", None) or []
                if not tool_calls:
                    # No tool calls — agent is done or gave text response
                    messages.append({"role": "assistant", "content": response.content})
                    break

                # Build assistant message with tool calls
                assistant_msg: dict = {"role": "assistant", "content": response.content or ""}
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in tool_calls
                ]
                messages.append(assistant_msg)

                # Execute each tool call
                for tc in tool_calls:
                    tool_name = tc.name
                    args = tc.arguments

                    # Log tool call details for debugging
                    args_preview = json.dumps(args)[:500] if isinstance(args, dict) else str(args)[:500]
                    logger.info(
                        "Output agent tool call: %s args=%s",
                        tool_name, args_preview,
                    )

                    # Skip tool calls with malformed arguments
                    if isinstance(args, dict) and "_raw_content" in args:
                        logger.warning(
                            "Skipping tool %s — malformed arguments (raw fallback)",
                            tool_name,
                        )
                        result = {"error": "Tool call had malformed JSON arguments. Please retry with valid JSON."}
                    elif tool_name in SANDBOX_TOOL_NAMES:
                        try:
                            result = await execute_sandbox_tool(
                                tool_name, args, sandbox,
                                api_keys=api_keys,
                                event_sink=self._event_sink,
                                artifact_ctx=artifact_ctx,
                            )
                        except Exception as tool_err:
                            logger.warning("Tool %s failed: %s", tool_name, tool_err)
                            result = {"error": str(tool_err)}
                    else:
                        result = {"error": f"Unknown tool: {tool_name}"}

                    logger.info("Output agent tool result: %s", json.dumps(result)[:500])

                    # Emit validation event for inspect_artifact
                    if tool_name == "inspect_artifact":
                        await self._emit("deep_work_engine_output_validation", {
                            "iteration": iteration + 1,
                            "pages": result.get("pages"),
                            "issues": result.get("issues", []),
                            "passed": result.get("passed", False),
                        })

                    if tool_name == "publish_artifact" and result.get("status") == "published":
                        published = True
                        self._artifacts.append({
                            "artifact_uri": result.get("artifact_uri"),
                            "download_url": result.get("download_url"),
                            "filename": result.get("filename"),
                            "size": result.get("size_bytes"),
                        })
                        await self._persist_engine_state()

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result),
                    })

                if published:
                    break

            if not published:
                logger.warning("Output agent did not publish — delivering markdown only")
                await self._emit("deep_work_engine_output_complete", {
                    "artifact_uri": None, "download_url": None, "preview_url": None,
                })
        finally:
            await executor.teardown(self._session_id, "output_agent")

    # ── Helpers ──────────────────────────────────────────────────

    def _format_intake_context(
        self,
        query: str,
        available_kbs: list[str],
        available_connectors: list[str],
    ) -> str:
        parts = [f"User Query: {query}"]
        if available_kbs:
            parts.append(f"Available Knowledge Bases: {', '.join(available_kbs)}")
        if available_connectors:
            parts.append(f"Available Connectors: {', '.join(available_connectors)}")
        return "\n\n".join(parts)

    def _collect_learnings(self, results: list[WorkerResult]) -> list[Learning]:
        learnings = []
        for r in results:
            learnings.extend(r.learnings)
        return learnings

    async def _persist_results(
        self, plan: DeepWorkPlan, learnings: list[Learning], report: str,
    ) -> None:
        await self._session_storage.write(
            self._tenant_id, self._session_id, "engine_report.md", report,
        )
        await self._session_storage.write(
            self._tenant_id, self._session_id, "engine_learnings.json",
            json.dumps([l.model_dump() for l in learnings]),
        )
        # Write report to workspace so it appears in the files explorer
        session_path = self._session_storage._get_session_path(
            self._tenant_id, self._session_id,
        )
        workspace = session_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "report.md").write_text(report, encoding="utf-8")
        await self._emit("workspace_update", {
            "action": "create",
            "path": "report.md",
            "size": len(report.encode("utf-8")),
        })

    async def _emit(self, event_type: str, data: dict) -> None:
        await self._event_sink.emit(event_type, data)

    async def _set_phase(self, phase: str) -> None:
        """Update pipeline phase, emit SSE event, and persist for refresh recovery."""
        self._current_phase = phase
        await self._emit("deep_work_engine_phase", {"phase": phase})
        await self._persist_engine_state()

    async def _persist_engine_state(
        self, reflection: dict | None = None,
    ) -> None:
        """Write incremental engine state to engine_state.json for refresh recovery."""
        state = {
            "completed_workers": self._completed_workers,
            "sources": self._accumulated_sources,
            "phase": self._current_phase,
            "artifacts": self._artifacts,
        }
        if reflection is not None:
            state["reflection"] = reflection
        await self._session_storage.write(
            self._tenant_id, self._session_id, "engine_state.json",
            json.dumps(state),
        )


# ── Module-level helpers ────────────────────────────────────────

_PROMPT_CACHE: dict[str, str] = {}


def _load_prompt(filename: str) -> str:
    if filename not in _PROMPT_CACHE:
        _PROMPT_CACHE[filename] = (
            Path(__file__).parent / "prompts" / filename
        ).read_text()
    return _PROMPT_CACHE[filename]


def _parse_json_response(content: str, model_cls: type[BaseModel]) -> Any:
    """Parse JSON from LLM response with fallback for markdown-wrapped output."""
    try:
        return model_cls.model_validate_json(content)
    except (ValidationError, ValueError):
        # Try code fence first (more precise)
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if match:
            return model_cls.model_validate_json(match.group(1))
        # Fall back to greedy brace match
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            return model_cls.model_validate_json(match.group())
        raise


def _is_retryable(exc: Exception) -> bool:
    """Classify error as retryable using exception types."""
    import litellm.exceptions as le
    if isinstance(exc, (le.RateLimitError, le.Timeout, le.APIConnectionError, le.ServiceUnavailableError)):
        return True
    status_code = getattr(exc, "status_code", None)
    if status_code in (429, 500, 502, 503, 504):
        return True
    return False
