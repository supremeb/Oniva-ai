import contextlib
import logging
from typing import TYPE_CHECKING

import litellm
from pydantic_ai import Agent
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic import BaseModel
from typing import Optional, Dict, Any, Type, Union, List
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

if TYPE_CHECKING:
    from app.services.rag_service import RAGService

from app.config import get_settings
from app.models.api_key import APIKey
from app.core.encryption import get_encryption_service

logger = logging.getLogger(__name__)


class AgentDependencies(BaseModel):
    """
    Dependencies injected into agents.
    Provides access to database, configuration, user context, and storage.

    Simplified Storage Pattern:
    - Volumes: Pre-warmed input data (read-only, shared across sessions)
    - Session: Working state with DBOS checkpointing
    - Artifacts: Persistent outputs with 7-year retention
    """
    db: Any  # AsyncSession (can't use type hint due to Pydantic limitation)
    user_id: Union[str, UUID]
    tenant_id: Optional[Union[str, UUID]] = None  # Tenant ID for multi-tenancy
    api_keys: Dict[str, str] = {}  # provider -> decrypted API key
    connectors: Dict[str, Dict[str, Any]] = {}  # connector_id -> config
    skill_call_stack: List[str] = []  # Stack of skill names for cycle detection
    session_id: Optional[str] = None  # Session ID for recitation context and storage
    mounted_volumes: List[str] = []  # Volume names mounted to this session
    agent_id: Optional[str] = None  # Agent ID for per-agent file isolation
    specific_sites: Optional[List[str]] = None  # Domain restrictions for web search
    project_instructions: Optional[str] = None  # Project-scoped instructions for LLM context
    project_knowledge_base_id: Optional[str] = None  # Project KB for RAG
    project_default_model: Optional[str] = None  # Project-level default model
    project_name: Optional[str] = None  # Project name for user-facing context
    project_file_names: Optional[List[str]] = None  # List of ready file names in the project
    rag_service: Optional[Any] = None  # Type: Optional[RAGService] — use TYPE_CHECKING import for IDE support


class BaseAgent:
    """
    Base class for all Oniva agents
    Handles LLM provider configuration with support for OpenRouter and direct providers
    """

    def __init__(
        self,
        name: str,
        description: str,
        output_type: Type[BaseModel],
        system_prompt: str,
        model: Optional[str] = None,
        **agent_kwargs
    ):
        self.name = name
        self.description = description
        self.system_prompt = system_prompt
        self.output_type = output_type

        settings = get_settings()

        # Normalize model to LiteLLM format (provider/model-name)
        from app.services.llm_gateway import normalize_model_id
        self.model = normalize_model_id(model or settings.DEFAULT_LLM_MODEL)

        # Create Pydantic AI agent with a placeholder model.
        # The actual model (with resolved API key) is passed at runtime via run(model=...).
        # This allows per-request API key resolution from the tenant's configured providers.
        from pydantic_ai import ModelSettings

        agent_settings = ModelSettings(
            temperature=agent_kwargs.pop('temperature', None),
            max_tokens=agent_kwargs.pop('max_tokens', None),
        )

        # Store retries value for per-invocation FunctionToolset construction
        self._agent_retries = 3

        # Use "test" as placeholder - always overridden at runtime
        self.agent = Agent(
            "test",
            output_type=output_type,
            system_prompt=system_prompt,
            deps_type=AgentDependencies,
            model_settings=agent_settings,
            retries=self._agent_retries,
            **agent_kwargs
        )

    async def _build_context_with_recitation(
        self,
        user_message: str,
        session_id: Optional[str],
        user_id: Union[str, UUID],
        tenant_id: Optional[Union[str, UUID]] = None,
    ) -> str:
        """
        Build context with plan/todo recitation if enabled.

        The recitation pattern (from Manus AI research) achieves 42% error reduction
        by including plan.md and todo.md in every agent turn, keeping agents focused
        on high-level goals during long sessions.

        Args:
            user_message: The user's message
            session_id: Session ID for loading recitation context
            user_id: User ID for session lookup
            tenant_id: Tenant ID for storage isolation

        Returns:
            User message with optional recitation context prepended
        """
        settings = get_settings()

        # Check if recitation is enabled
        if not getattr(settings, 'USE_PLAN_TODO_RECITATION', False):
            return user_message

        if not session_id:
            return user_message

        try:
            from app.services.session_memory import get_recitation_context
            resolved_tenant = str(tenant_id) if tenant_id else "unknown"
            recitation = await get_recitation_context(resolved_tenant, session_id, str(user_id))

            # Skip prepending if recitation is empty (default templates)
            if not recitation or not recitation.strip():
                return user_message

            # Prepend recitation context to user message
            return f"{recitation}\n\n---\n\nUser Request:\n{user_message}"
        except Exception:
            # If recitation fails, continue without it
            return user_message

    async def _build_context_with_memory(
        self,
        user_message: str,
        session_id: Optional[str],
        user_id: Union[str, UUID],
        tenant_id: Optional[Union[str, UUID]],
        db: Any,
        agent_id: Optional[UUID] = None,
        active_knowledge_base_ids: Optional[list] = None,
        project_instructions: Optional[str] = None,
        project_knowledge_base_id: Optional[str] = None,
        project_name: Optional[str] = None,
        project_file_names: Optional[List[str]] = None,
        rag_service: Optional[Any] = None,
        rag_sink: Optional[list] = None,
    ) -> str:
        """
        Build context with recitation, preferences, learnings, and episodic memory.

        Token Budget (from Context Optimization research):
        - Recitation: ~1,500 tokens
        - User preferences: ~300 tokens
        - Agent learnings: ~400 tokens (3 entries)
        - Episodic memory: ~600 tokens (3 entries)
        - Org context: ~500 tokens (profile + 3 shared memories)
        - Total overhead: ~3,300 tokens

        Graceful Degradation: Agent continues if any memory retrieval fails.

        Args:
            user_message: The user's message
            session_id: Session ID for recitation context
            user_id: User ID for memory lookup
            tenant_id: Tenant ID for multi-tenancy
            db: Database session
            agent_id: Optional agent ID for scoped learning retrieval

        Returns:
            Enhanced message with context prepended
        """
        settings = get_settings()
        context_parts = []

        # 1. Existing recitation pattern (HIGH priority)
        if getattr(settings, 'USE_PLAN_TODO_RECITATION', False) and session_id:
            try:
                from app.services.session_memory import get_recitation_context
                resolved_tenant = str(tenant_id) if tenant_id else "unknown"
                recitation = await get_recitation_context(resolved_tenant, session_id, str(user_id))
                if recitation and recitation.strip():
                    context_parts.append(recitation)
            except Exception as e:
                logger.warning(f"Recitation failed: {e}")

        # 2. User preferences (MEDIUM priority, ~300 tokens)
        if getattr(settings, 'USE_USER_PREFERENCES', False) and tenant_id:
            try:
                from app.services.user_preferences import get_user_preferences_service
                prefs_service = get_user_preferences_service()
                prefs = await prefs_service.get_preferences(
                    db, UUID(str(tenant_id)), UUID(str(user_id))
                )
                prefs_text = prefs_service.format_for_context(prefs)
                context_parts.append(prefs_text)
            except Exception as e:
                logger.warning(f"Preferences failed: {e}")

        # 3. Agent learnings (MEDIUM-HIGH priority, ~400 tokens, 3 entries max)
        if getattr(settings, 'USE_AGENT_LEARNING', False) and tenant_id:
            try:
                from app.services.agent_learning import get_agent_learning_service
                learning_service = get_agent_learning_service()
                learnings = await learning_service.search_relevant_learnings(
                    db=db,
                    tenant_id=UUID(str(tenant_id)),
                    query=user_message,
                    agent_id=agent_id,
                    limit=3,
                    min_similarity=0.6,
                )
                if learnings:
                    learning_text = learning_service.format_for_context(learnings)
                    context_parts.append(learning_text)
            except Exception as e:
                logger.warning(f"Agent learning retrieval failed: {e}")

        # 4. Episodic memory (MEDIUM priority, ~600 tokens, 3 entries max)
        # Skip implicit injection when agent has explicit memory tools (avoids duplicates)
        from app.agents.skills.registry import get_default_skills
        agent_has_memory_tool = agent_id and 'search_past_decisions' in get_default_skills(agent_id or '')
        if getattr(settings, 'USE_EPISODIC_MEMORY', False) and tenant_id and not agent_has_memory_tool:
            try:
                from app.services.episodic_memory import get_episodic_memory_service
                episodic_service = get_episodic_memory_service()
                similar = await episodic_service.recall_similar(
                    db=db,
                    tenant_id=UUID(str(tenant_id)),
                    user_id=UUID(str(user_id)),
                    query=user_message,
                    limit=3,  # Token budget constraint
                    min_similarity=0.85,  # Higher precision per research
                )
                if similar:
                    memory_text = episodic_service.format_for_context(similar)
                    context_parts.append(memory_text)
            except Exception as e:
                logger.warning(f"Episodic memory failed: {e}")

        # 4a. Project context (name, instructions, files)
        is_project_chat = bool(project_name)
        from app.services.agent_resolver import build_project_context
        project_block = build_project_context(project_name, project_instructions, project_file_names)
        if project_block:
            context_parts.append(project_block)

        # Merge project KB into active KBs for RAG
        if project_knowledge_base_id:
            if active_knowledge_base_ids is None:
                active_knowledge_base_ids = []
            if project_knowledge_base_id not in active_knowledge_base_ids:
                active_knowledge_base_ids = [project_knowledge_base_id] + list(active_knowledge_base_ids)

        # 4b. Knowledge Base RAG (auto-inject if KB toggled)
        if active_knowledge_base_ids and db and tenant_id:
            import asyncio
            try:
                if rag_service is None:
                    from app.services.rag_service import RAGService
                    rag_service = RAGService(db)
                # For project chats with short follow-up queries, augment with project
                # context so vector search has enough semantic signal to find relevant docs.
                rag_query = user_message
                if is_project_chat and len(user_message.split()) < 10:
                    context_parts_for_query = []
                    if project_name:
                        context_parts_for_query.append(project_name)
                    if project_file_names:
                        context_parts_for_query.append(" ".join(project_file_names[:5]))
                    if context_parts_for_query:
                        rag_query = f"{' '.join(context_parts_for_query)} {user_message}"
                rag_results = await asyncio.wait_for(
                    rag_service.hybrid_search_v2(
                        query=rag_query,
                        tenant_id=UUID(str(tenant_id)),
                        knowledge_base_ids=[UUID(str(kb_id)) for kb_id in active_knowledge_base_ids[:3]],
                        limit=10,
                    ),
                    timeout=2.5,
                )
                if rag_results:
                    source_label = "File" if is_project_chat else "Source"
                    rag_context = self._format_rag_results(rag_results, max_tokens=3000, source_label=source_label)
                    # Use user-friendly heading based on context
                    rag_heading = "## Relevant Content from Project Files" if is_project_chat else "## Relevant Knowledge Base Documents"
                    context_parts.append(f"\n{rag_heading}\n{rag_context}")
                    # Hand raw results to the caller's request-scoped sink so chat.py
                    # can emit SSE source events. Never stored on the shared agent
                    # singleton (that leaked sources across concurrent tenants).
                    if rag_sink is not None:
                        rag_sink.extend(rag_results)
            except asyncio.TimeoutError:
                logger.warning("Auto-RAG query timed out (>2.5s), continuing without KB context")
                # Timeout may leave the DB session in a bad state; rollback to recover
                try:
                    await db.rollback()
                    if tenant_id:
                        from app.core.database import set_tenant_context
                        await set_tenant_context(db, str(tenant_id))
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"Auto-RAG query failed: {e}, continuing without KB context")

        # 5. Organization context (shared memories + org profile)
        if getattr(settings, 'USE_ORG_CONTEXT', False) and tenant_id:
            try:
                # 5a. Org profile context (industry, compliance, default context)
                from app.models.tenant import Tenant
                tenant_result = await db.execute(
                    select(Tenant).where(Tenant.id == UUID(str(tenant_id)))
                )
                tenant = tenant_result.scalar_one_or_none()
                if tenant:
                    tenant_settings = tenant.settings or {}
                    org_profile = tenant_settings.get('org_profile', {})
                    if org_profile.get('default_context'):
                        context_parts.append(
                            f"## Organization Context\n{org_profile['default_context']}"
                        )

                # 5b. Shared memories from the org
                from app.services.episodic_memory import get_episodic_memory_service
                episodic_service = get_episodic_memory_service()
                shared = await episodic_service.recall_shared(
                    db=db,
                    tenant_id=UUID(str(tenant_id)),
                    query=user_message,
                    limit=3,
                )
                if shared:
                    shared_text = episodic_service.format_shared_for_context(shared)
                    context_parts.append(shared_text)
            except Exception as e:
                logger.warning(f"Org context injection failed: {e}")

        # 6. Tenant capabilities (feature flags context for the agent)
        if tenant_id:
            try:
                from app.services.feature_flags import get_tenant_features, FLAG_METADATA
                from app.models.tenant import Tenant
                from sqlalchemy import select as sa_select
                tenant_result = await db.execute(
                    sa_select(Tenant).where(Tenant.id == UUID(str(tenant_id)))
                )
                tenant_obj = tenant_result.scalar_one_or_none()
                if tenant_obj:
                    features = get_tenant_features(tenant_obj)
                    enabled_groups = sorted({
                        FLAG_METADATA[f].group
                        for f, v in features.items()
                        if v and f in FLAG_METADATA
                    })
                    if enabled_groups:
                        context_parts.append(
                            f"<tenant-capabilities>\nEnabled feature groups: {', '.join(enabled_groups)}\n</tenant-capabilities>"
                        )
            except Exception as e:
                logger.debug(f"Tenant capabilities injection skipped: {e}")

        # 7. User message (always included)
        if context_parts:
            return "\n".join(context_parts) + f"\n\n---\n\nUser Request:\n{user_message}"
        return user_message

    @staticmethod
    def _format_rag_results(results: list, max_tokens: int = 3000, source_label: str = "Source") -> str:
        """Format RAG search results for context injection with token budget."""
        formatted = []
        approx_tokens = 0
        for r in results:
            chunk_text = r.get("text", r.get("content", ""))
            source = r.get("source", r.get("document_name", "Unknown"))
            entry = f"**{source_label}:** {source}\n{chunk_text}"
            entry_tokens = len(entry) // 4  # rough token estimate
            if approx_tokens + entry_tokens > max_tokens:
                break
            formatted.append(entry)
            approx_tokens += entry_tokens
        return "\n\n---\n\n".join(formatted) if formatted else ""

    async def store_decision_to_memory(
        self,
        db: Any,
        tenant_id: Union[str, UUID],
        user_id: Union[str, UUID],
        context: str,
        decision: str,
        session_id: Optional[str] = None,
        action_type: str = "analysis",
        rationale: Optional[str] = None,
        entities: Optional[List[str]] = None,
    ) -> Optional[UUID]:
        """
        Store a decision to episodic memory.

        Call this after making a significant decision to enable cross-session learning.

        Args:
            db: Database session
            tenant_id: Tenant ID for multi-tenancy
            user_id: User ID
            context: Context of the decision (what was asked)
            decision: The decision made
            session_id: Optional session ID for linking
            action_type: Type of action (analysis, compliance_check, recommendation, research, chat, other)
            rationale: Reasoning behind the decision
            entities: Entities involved (tickers, companies, etc.)

        Returns:
            UUID of stored memory, or None if feature disabled or failed
        """
        settings = get_settings()
        if not getattr(settings, 'USE_EPISODIC_MEMORY', False):
            return None

        try:
            from app.services.episodic_memory import get_episodic_memory_service
            service = get_episodic_memory_service()
            memory_id = await service.store_decision(
                db=db,
                tenant_id=UUID(str(tenant_id)),
                user_id=UUID(str(user_id)),
                agent_name=self.name,
                action_type=action_type,
                context=context,
                decision=decision,
                session_id=session_id,
                rationale=rationale,
                entities=entities,
            )
            return memory_id
        except Exception as e:
            logger.warning(f"Failed to store decision: {e}")
            return None

    async def get_api_key(
        self,
        provider: str,
        db: AsyncSession,
        user_id: Union[str, UUID]
    ) -> Optional[str]:
        """
        Retrieve decrypted API key for a provider
        """
        # First try user's configured key
        stmt = (
            select(APIKey)
            .where(APIKey.user_id == user_id)
            .where(APIKey.provider == provider)
            .where(APIKey.is_active == True)
            .order_by(APIKey.is_default.desc(), APIKey.created_at.desc())
        )

        result = await db.execute(stmt)
        # Use scalars().first() to handle potential duplicates gracefully
        # The ORDER BY ensures we get the default/most recent key first
        api_key_record = result.scalars().first()

        if api_key_record:
            encryption = get_encryption_service()
            return encryption.decrypt(api_key_record.encrypted_key)

        # Fall back to environment variable
        settings = get_settings()
        if provider == "openrouter":
            return settings.OPENROUTER_API_KEY
        elif provider == "openai":
            return settings.OPENAI_API_KEY
        elif provider == "anthropic":
            return settings.ANTHROPIC_API_KEY

        return None

    async def _load_tool_api_keys(
        self,
        db: AsyncSession,
        tenant_id: Union[str, UUID],
        api_keys: Dict[str, str],
    ) -> None:
        """
        Load tool-level API keys (search providers, etc.) from the api_keys table
        and merge them into the api_keys dict. LLM keys already present take precedence.
        """
        import os
        tool_providers = ("tavily", "parallelai", "exa", "serpapi", "e2b")
        try:
            result = await db.execute(
                select(APIKey).where(
                    APIKey.tenant_id == UUID(str(tenant_id)),
                    APIKey.is_active == True,  # noqa: E712
                    APIKey.provider.in_(tool_providers),
                )
            )
            encryption = get_encryption_service()
            for row in result.scalars():
                if row.provider not in api_keys:
                    api_keys[row.provider] = encryption.decrypt(row.encrypted_key)
        except Exception:
            logger.debug("Failed to load tool keys from api_keys table", exc_info=True)

        # Env var fallbacks for search providers
        env_map = {
            "tavily": "TAVILY_API_KEY",
            "parallelai": "PARALLEL_API_KEY",
            "exa": "EXA_API_KEY",
            "serpapi": "SERP_API_KEY",
        }
        for provider, env_var in env_map.items():
            if provider not in api_keys:
                val = os.environ.get(env_var, "")
                if val:
                    api_keys[provider] = val

    def _wrap_tool_with_observability(self, func, tool_name: str):
        """Wrap a tool function with observability tracing without altering signature.

        Used for agent-inline tools (defined in __init__, not via skills registry).
        Uses functools.wraps to preserve function metadata for Pydantic AI introspection.
        Skips wrapping if the function already has `_is_observed` (set by @skill decorator).
        """
        if getattr(func, '_is_observed', False):
            return func

        import functools

        @functools.wraps(func)
        async def traced_func(*args, **kwargs):
            from app.services.observability import get_observability_service
            obs = get_observability_service()
            async with obs.trace_tool_execution(
                tool_name=tool_name,
                tool_category="agent_tool",
                agent_type=self.name,
            ) as trace:
                try:
                    result = await func(*args, **kwargs)
                    trace.set_success(output_size=len(str(result)) if result is not None else 0)
                    return result
                except Exception as e:
                    trace.set_error(str(e))
                    raise

        traced_func._is_observed = True
        return traced_func

    async def _truncate_messages_if_needed(
        self,
        messages: list,
        model: str,
        session_id: Optional[str] = None,
        tenant_id: Optional[Union[str, UUID]] = None,
        system_prompt_tokens: int = 3300,
        safety_margin: int = 2000,
    ) -> list:
        """Truncate old messages to fit within model context window.

        Keeps system prompt + recitation context intact. Removes oldest messages
        first. Saves full history to session file for audit trail.
        Uses litellm.get_model_info() for context windows — no hardcoded dict.
        """
        import litellm

        try:
            model_info = litellm.get_model_info(model)
            window = model_info.get("max_input_tokens", 128_000)
        except Exception:
            window = 128_000

        available = window - system_prompt_tokens - safety_margin

        # Estimate total tokens (existing pattern: len(text) // 4)
        total = sum(len(str(msg)) // 4 for msg in messages)
        if total <= available:
            return messages

        # Save full history to session file before truncating
        if session_id:
            try:
                from app.services.session_storage import get_session_storage
                storage = get_session_storage()
                history_text = "\n".join(str(msg) for msg in messages)
                await storage.write(
                    str(tenant_id) if tenant_id else "unknown",
                    session_id,
                    f"logs/full_history_{len(messages)}_msgs.md",
                    history_text,
                )
            except Exception:
                pass  # Don't fail the agent run if history save fails

        # Keep most recent messages that fit
        kept: list = []
        running_tokens = 0
        for msg in reversed(messages):
            msg_tokens = len(str(msg)) // 4
            if running_tokens + msg_tokens > available:
                break
            kept.insert(0, msg)
            running_tokens += msg_tokens

        logger.info(
            f"Truncated message history from {len(messages)} to {len(kept)} messages "
            f"(~{total} -> ~{running_tokens} tokens, window={window})"
        )
        return kept

    async def run(
        self,
        user_message: str,
        db: AsyncSession,
        user_id: Union[str, UUID],
        session_id: Optional[str] = None,
        tenant_id: Optional[Union[str, UUID]] = None,
        **kwargs
    ):
        """
        Run the agent with user message

        Args:
            user_message: The user's message
            db: Database session
            user_id: User ID
            session_id: Optional session ID for recitation context
            tenant_id: Optional tenant ID for multi-tenancy
            **kwargs: Additional arguments:
                - model_override: Override the agent's default model
                - enable_thinking: Enable extended thinking for supported models
                - agent_id: Agent ID for scoped learning
        """
        from app.services.llm_gateway import (
            resolve_all_llm_keys, create_litellm_model, resolve_model_and_key,
        )

        # Pop our custom kwargs before passing to agent.run()
        model_override = kwargs.pop('model_override', None)
        enable_thinking = kwargs.pop('enable_thinking', False)
        connectors = kwargs.pop('connectors', None)
        extra_tools = kwargs.pop('extra_tools', None)
        active_knowledge_base_ids = kwargs.pop('active_knowledge_base_ids', None)
        specific_sites = kwargs.pop('specific_sites', None)
        project_instructions = kwargs.pop('project_instructions', None)
        project_knowledge_base_id = kwargs.pop('project_knowledge_base_id', None)
        project_default_model = kwargs.pop('project_default_model', None)
        project_name = kwargs.pop('project_name', None)
        project_file_names = kwargs.pop('project_file_names', None)
        web_search_instruction = kwargs.pop('web_search_instruction', None)

        # Resolve all provider keys in one call (replaces 3-query loop)
        api_keys = {}
        if tenant_id:
            api_keys = await resolve_all_llm_keys(db, UUID(str(tenant_id)))

        # Fall back to legacy per-user keys if no tenant keys found
        if not api_keys:
            for provider in ["openrouter", "openai", "anthropic"]:
                key = await self.get_api_key(provider, db, user_id)
                if key:
                    api_keys[provider] = key

        # Load tool-level keys (search providers, etc.) from api_keys table
        if tenant_id:
            await self._load_tool_api_keys(db, tenant_id, api_keys)

        # Pop agent_id BEFORE deps construction so it's available for both
        agent_id = kwargs.pop('agent_id', None)

        # Create dependencies
        deps = AgentDependencies(
            db=db,
            user_id=user_id,
            tenant_id=tenant_id,
            api_keys=api_keys,
            session_id=session_id,
            connectors=connectors or {},
            agent_id=agent_id,
            specific_sites=specific_sites,
            project_instructions=project_instructions,
            project_knowledge_base_id=project_knowledge_base_id,
            project_default_model=project_default_model,
            project_name=project_name,
            project_file_names=project_file_names,
        )

        # Build context with full memory stack (recitation + preferences + learnings + episodic + RAG + project)
        enhanced_message = await self._build_context_with_memory(
            user_message, session_id, user_id, tenant_id, db,
            agent_id=agent_id,
            active_knowledge_base_ids=active_knowledge_base_ids,
            project_instructions=project_instructions,
            project_knowledge_base_id=project_knowledge_base_id,
            project_name=project_name,
            project_file_names=project_file_names,
        )

        # Resolve effective model + API key (override > project > tenant > agent default)
        effective_model, api_key, provider_type = await resolve_model_and_key(
            db=db,
            tenant_id=UUID(str(tenant_id)) if tenant_id else None,
            api_keys=api_keys,
            model_override=model_override,
            project_default_model=project_default_model,
            agent_default_model=self.model,
        )

        logger.info(f"Agent using model={effective_model!r} (override={model_override!r}, provider={provider_type!r})")

        # Wrap extra tools with harness (loop detection + credential scanning + recitation).
        # Replaces TodoMiddleware. Per-run closure state ensures concurrency safety.
        if extra_tools and session_id and get_settings().USE_HARNESS_PIPELINE:
            from app.agents.harness import wrap_tools_with_harness
            extra_tools = wrap_tools_with_harness(
                extra_tools, session_id, str(tenant_id) if tenant_id else "",
            )

        # Build per-invocation toolset for extra tools (skills/connectors).
        # This is additive — agent's static @tool decorators remain available
        # alongside this dynamic toolset via the toolsets= parameter.
        # Sort by name for deterministic tool ordering — maximises KV-cache reuse
        # across identical agent configurations (same tools in same order = same
        # prompt prefix = cache hit).
        request_toolset = FunctionToolset()
        if extra_tools:
            extra_tools = sorted(
                extra_tools,
                key=lambda f: getattr(f, '__name__', None) or getattr(f, '__skill_name__', 'unknown'),
            )
            from pydantic_ai.tools import Tool as PydanticTool
            for func in extra_tools:
                if isinstance(func, PydanticTool):
                    # Already a Tool object (e.g., workflow tools) — add directly
                    request_toolset.tools[func.name] = func
                else:
                    tool_name = getattr(func, '__name__', None) or getattr(func, '__skill_name__', 'unknown')
                    # Wrap with observability for agent-inline tools
                    func = self._wrap_tool_with_observability(func, tool_name)
                    tool = PydanticTool(func, takes_ctx=True, max_retries=self._agent_retries)
                    request_toolset.tools[tool.name] = tool

        run_toolsets = [request_toolset] if request_toolset.tools else []

        # Enforce quota before LLM call
        if tenant_id:
            await self._check_quota(db, tenant_id, user_id, enhanced_message)

        # Check if we should use direct Anthropic thinking call
        # Works for both direct Anthropic models and OpenRouter-routed Claude models
        model_name_lower = effective_model.lower()
        is_anthropic_model = (
            provider_type == "anthropic"
            or "claude" in model_name_lower
        )
        if enable_thinking and is_anthropic_model:
            uses_openrouter = effective_model.startswith("openrouter/")

            if not uses_openrouter:
                # Direct Anthropic model — try direct SDK call for best thinking quality
                anthropic_key = api_keys.get("anthropic")
                if not anthropic_key:
                    settings = get_settings()
                    anthropic_key = getattr(settings, 'ANTHROPIC_API_KEY', None)
                if anthropic_key:
                    result = await self._run_with_anthropic_thinking(
                        enhanced_message, deps, effective_model, anthropic_key, **kwargs
                    )
                    if tenant_id:
                        await self._record_quota_usage(db, tenant_id, user_id, result)
                    return result

            # OpenRouter or no direct Anthropic key — use thinking extraction
            result = await self._run_with_litellm_thinking(
                enhanced_message, deps, effective_model, api_key, **kwargs
            )
            if tenant_id:
                await self._record_quota_usage(db, tenant_id, user_id, result)
            return result

        # Create LiteLLM-backed model with resolved key
        model = create_litellm_model(effective_model, api_key)

        # Truncate message history if it would overflow the context window
        if 'message_history' in kwargs and kwargs['message_history']:
            kwargs['message_history'] = await self._truncate_messages_if_needed(
                kwargs['message_history'], effective_model, session_id, tenant_id,
            )

        # Pass web search instruction as system-level instructions (not in user message).
        # Pydantic AI `instructions` is appended to the agent's system prompt.
        run_instructions = web_search_instruction or None

        # Run agent with dynamic model, passing per-invocation toolset
        result = await self.agent.run(
            enhanced_message, deps=deps, model=model,
            instructions=run_instructions,
            toolsets=run_toolsets or None,
            **kwargs,
        )
        if tenant_id:
            await self._record_quota_usage(db, tenant_id, user_id, result)
        return result

    async def stream_events(
        self,
        user_message: str,
        db: AsyncSession,
        user_id: Union[str, UUID],
        session_id: Optional[str] = None,
        tenant_id: Optional[Union[str, UUID]] = None,
        **kwargs
    ):
        """
        Stream pydantic-ai events from the agent.

        Same setup as run() (model resolution, API key resolution, tool registration)
        but yields events from agent.run_stream_events() instead of blocking on run().

        Yields pydantic-ai AgentStreamEvent instances (PartStartEvent, PartDeltaEvent,
        FunctionToolCallEvent, FunctionToolResultEvent, FinalResultEvent, etc.).
        The final item yielded is an AgentRunResultEvent with the complete result.
        """
        from app.services.llm_gateway import (
            resolve_all_llm_keys, create_litellm_model, resolve_model_and_key,
        )

        # Pop custom kwargs
        model_override = kwargs.pop('model_override', None)
        enable_thinking = kwargs.pop('enable_thinking', False)
        connectors = kwargs.pop('connectors', None)
        extra_tools = kwargs.pop('extra_tools', None)
        active_knowledge_base_ids = kwargs.pop('active_knowledge_base_ids', None)
        specific_sites = kwargs.pop('specific_sites', None)
        project_instructions = kwargs.pop('project_instructions', None)
        project_knowledge_base_id = kwargs.pop('project_knowledge_base_id', None)
        project_default_model = kwargs.pop('project_default_model', None)
        project_name = kwargs.pop('project_name', None)
        project_file_names = kwargs.pop('project_file_names', None)
        web_search_instruction = kwargs.pop('web_search_instruction', None)
        rag_sink = kwargs.pop('rag_sink', None)

        # Resolve API keys
        api_keys = {}
        if tenant_id:
            api_keys = await resolve_all_llm_keys(db, UUID(str(tenant_id)))
        if not api_keys:
            for provider in ["openrouter", "openai", "anthropic"]:
                key = await self.get_api_key(provider, db, user_id)
                if key:
                    api_keys[provider] = key

        # Load tool-level keys (search providers, etc.) from api_keys table
        if tenant_id:
            await self._load_tool_api_keys(db, tenant_id, api_keys)

        # Pop agent_id BEFORE deps construction so it's available for both
        agent_id = kwargs.pop('agent_id', None)

        deps = AgentDependencies(
            db=db, user_id=user_id, tenant_id=tenant_id,
            api_keys=api_keys, session_id=session_id,
            connectors=connectors or {},
            agent_id=agent_id,
            specific_sites=specific_sites,
            project_instructions=project_instructions,
            project_knowledge_base_id=project_knowledge_base_id,
            project_default_model=project_default_model,
            project_name=project_name,
            project_file_names=project_file_names,
        )

        enhanced_message = await self._build_context_with_memory(
            user_message, session_id, user_id, tenant_id, db,
            agent_id=agent_id,
            active_knowledge_base_ids=active_knowledge_base_ids,
            project_instructions=project_instructions,
            project_knowledge_base_id=project_knowledge_base_id,
            project_name=project_name,
            project_file_names=project_file_names,
            rag_sink=rag_sink,
        )

        # Resolve effective model + API key (override > project > tenant > agent default)
        effective_model, api_key, provider_type = await resolve_model_and_key(
            db=db,
            tenant_id=UUID(str(tenant_id)) if tenant_id else None,
            api_keys=api_keys,
            model_override=model_override,
            project_default_model=project_default_model,
            agent_default_model=self.model,
        )

        # Wrap extra tools with harness (loop detection + credential scanning + recitation).
        # stream_events() previously lacked recitation — this fixes that gap.
        if extra_tools and session_id and get_settings().USE_HARNESS_PIPELINE:
            from app.agents.harness import wrap_tools_with_harness
            extra_tools = wrap_tools_with_harness(
                extra_tools, session_id, str(tenant_id) if tenant_id else "",
            )

        # Build per-invocation toolset for extra tools (skills/connectors).
        # This is additive — agent's static @tool decorators remain available.
        request_toolset = FunctionToolset()
        if extra_tools:
            extra_tools = sorted(
                extra_tools,
                key=lambda f: getattr(f, '__name__', None) or getattr(f, '__skill_name__', 'unknown'),
            )
            from pydantic_ai.tools import Tool as PydanticTool
            for func in extra_tools:
                if isinstance(func, PydanticTool):
                    # Already a Tool object (e.g., workflow tools) — add directly
                    request_toolset.tools[func.name] = func
                else:
                    tool_name = getattr(func, '__name__', None) or getattr(func, '__skill_name__', 'unknown')
                    func = self._wrap_tool_with_observability(func, tool_name)
                    tool = PydanticTool(func, takes_ctx=True, max_retries=self._agent_retries)
                    request_toolset.tools[tool.name] = tool

        run_toolsets = [request_toolset] if request_toolset.tools else []

        if tenant_id:
            await self._check_quota(db, tenant_id, user_id, enhanced_message)

        model = create_litellm_model(effective_model, api_key)

        if 'message_history' in kwargs and kwargs['message_history']:
            kwargs['message_history'] = await self._truncate_messages_if_needed(
                kwargs['message_history'], effective_model, session_id, tenant_id,
            )

        # Build model_settings for extended thinking in streaming mode
        model_settings = None
        if enable_thinking:
            model_name_lower = effective_model.lower()
            is_anthropic = (
                provider_type == "anthropic"
                or "claude" in model_name_lower
            )
            if is_anthropic:
                from pydantic_ai.models.anthropic import AnthropicModelSettings
                model_settings = AnthropicModelSettings(
                    anthropic_thinking={'type': 'enabled', 'budget_tokens': 10000},
                )
            else:
                # For non-Anthropic models that support thinking (e.g. DeepSeek),
                # pass as generic model_settings dict
                model_settings = {'thinking': {'type': 'enabled', 'budget_tokens': 10000}}

        # Pass web search instruction as system-level instructions (not in user message).
        # Pydantic AI `instructions` is appended to the agent's system prompt.
        run_instructions = web_search_instruction or None

        async for event in self.agent.run_stream_events(
            enhanced_message, deps=deps, model=model,
            model_settings=model_settings,
            instructions=run_instructions,
            toolsets=run_toolsets or None,
            **kwargs
        ):
            yield event

    async def _check_quota(
        self,
        db: AsyncSession,
        tenant_id: Union[str, UUID],
        user_id: Union[str, UUID],
        message: str,
    ) -> None:
        """Check tenant/user quota before an LLM call. Raises 429 on exceeded."""
        from app.services.quota_service import QuotaService
        from app.models.usage_quota import QuotaType

        estimated_tokens = len(message) // 4
        quota_svc = QuotaService(db)
        allowed, reason = await quota_svc.check_quota(
            UUID(str(tenant_id)), UUID(str(user_id)),
            QuotaType.LLM_TOKENS, estimated_tokens,
        )
        if not allowed:
            from fastapi import HTTPException
            raise HTTPException(status_code=429, detail=reason)

    async def _record_quota_usage(
        self,
        db: AsyncSession,
        tenant_id: Union[str, UUID],
        user_id: Union[str, UUID],
        result,
    ) -> None:
        """Increment quota usage after a successful LLM call."""
        from app.services.quota_service import QuotaService
        from app.models.usage_quota import QuotaType

        try:
            cost = getattr(result, 'cost', None)
            if cost and hasattr(cost, 'total_tokens') and cost.total_tokens:
                tokens = cost.total_tokens
            else:
                tokens = len(str(result.output)) // 4
            quota_svc = QuotaService(db)
            await quota_svc.increment_usage(
                UUID(str(tenant_id)), UUID(str(user_id)),
                QuotaType.LLM_TOKENS, tokens,
            )
        except Exception:
            logger.warning("Failed to record quota usage", exc_info=True)

    async def _run_with_anthropic_thinking(
        self,
        message: str,
        deps: AgentDependencies,
        model_id: str,
        api_key: str,
        **kwargs,
    ):
        """
        Run with Anthropic extended thinking enabled.

        Uses the Anthropic SDK directly to get thinking blocks,
        then falls back to pydantic-ai for structured output parsing.
        We extract the thinking content and attach it to the result.
        """
        from app.services.llm_gateway import create_litellm_model

        model_name = model_id.split("/", 1)[1] if "/" in model_id else model_id

        # Make a direct Anthropic call with thinking enabled to get reasoning
        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=api_key)

            thinking_response = await client.messages.create(
                model=model_name,
                max_tokens=16000,
                thinking={
                    "type": "enabled",
                    "budget_tokens": 10000,
                },
                messages=[{"role": "user", "content": message}],
                system=self.system_prompt,
            )

            # Extract thinking and text from the response
            reasoning_text = ""
            response_text = ""
            for block in thinking_response.content:
                if block.type == "thinking":
                    reasoning_text += block.thinking
                elif block.type == "text":
                    response_text += block.text

            # Now run through pydantic-ai for structured output
            model = create_litellm_model(model_id, api_key)
            result = await self.agent.run(message, deps=deps, model=model, **kwargs)

            # Attach the reasoning to the result
            if reasoning_text:
                result._reasoning = reasoning_text

            return result
        except Exception as e:
            logger.warning(f"Anthropic thinking call failed, falling back to standard: {e}")
            # Fall back to standard pydantic-ai call without thinking
            model = create_litellm_model(model_id, api_key)
            result = await self.agent.run(message, deps=deps, model=model, **kwargs)
            return result

    async def _run_with_litellm_thinking(
        self,
        message: str,
        deps: AgentDependencies,
        model_id: str,
        api_key: str,
        **kwargs,
    ):
        """
        Run with extended thinking, then pydantic-ai for structured output.

        OpenRouter: calls the API directly with the `reasoning` parameter
        (LiteLLM strips reasoning from responses, so we bypass it).
        Other providers: uses litellm.acompletion with `thinking` parameter.
        """
        from app.services.llm_gateway import create_litellm_model

        try:
            is_openrouter = model_id.startswith("openrouter/")
            reasoning_text = ""

            if is_openrouter:
                import httpx
                or_model = model_id.replace("openrouter/", "", 1)

                async with httpx.AsyncClient(timeout=120) as client:
                    resp = await client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": or_model,
                            "messages": [
                                {"role": "system", "content": self.system_prompt},
                                {"role": "user", "content": message},
                            ],
                            "max_tokens": 16000,
                            "reasoning": {"max_tokens": 10000},
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()

                choices = data.get("choices", [])
                if choices:
                    reasoning_text = choices[0].get("message", {}).get("reasoning", "") or ""
            else:
                thinking_response = await litellm.acompletion(
                    model=model_id,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": message},
                    ],
                    api_key=api_key,
                    max_tokens=16000,
                    thinking={"type": "enabled", "budget_tokens": 10000},
                )
                choices = getattr(thinking_response, 'choices', [])
                if choices:
                    msg = getattr(choices[0], 'message', None)
                    if msg:
                        reasoning_text = (
                            getattr(msg, 'reasoning_content', None)
                            or getattr(msg, 'reasoning', None)
                            or getattr(msg, 'thinking', None)
                            or ""
                        )

            # Run through pydantic-ai for structured output (with tools, etc.)
            model = create_litellm_model(model_id, api_key)
            result = await self.agent.run(message, deps=deps, model=model, **kwargs)

            if reasoning_text:
                result._reasoning = reasoning_text

            return result
        except Exception as e:
            logger.warning(f"Thinking extraction failed, falling back to standard: {e}")
            model = create_litellm_model(model_id, api_key)
            result = await self.agent.run(message, deps=deps, model=model, **kwargs)
            return result

    @contextlib.asynccontextmanager
    async def run_stream(
        self,
        user_message: str,
        db: AsyncSession,
        user_id: Union[str, UUID],
        session_id: Optional[str] = None,
        tenant_id: Optional[Union[str, UUID]] = None,
        **kwargs
    ):
        """
        Run the agent with streaming response.

        Full parity with run() — supports model_override, extra_tools (skills/
        connectors), and harness wrapping (loop detection, credential scanning, recitation).

        Args:
            user_message: The user's message
            db: Database session
            user_id: User ID
            session_id: Optional session ID for recitation context
            tenant_id: Optional tenant ID for multi-tenancy
            **kwargs: Additional arguments:
                - model_override: Override the agent's default model
                - enable_thinking: Enable extended thinking (logged, not yet streamed)
                - extra_tools: List of skill/connector tool functions
                - connectors: Dict of connector configs
                - agent_id: Agent ID for scoped learning
        """
        from app.services.llm_gateway import (
            resolve_all_llm_keys, create_litellm_model, resolve_model_and_key,
        )

        # Pop our custom kwargs before passing to agent.run_stream()
        model_override = kwargs.pop('model_override', None)
        enable_thinking = kwargs.pop('enable_thinking', False)
        connectors = kwargs.pop('connectors', None)
        extra_tools = kwargs.pop('extra_tools', None)
        active_knowledge_base_ids = kwargs.pop('active_knowledge_base_ids', None)
        specific_sites = kwargs.pop('specific_sites', None)
        project_instructions = kwargs.pop('project_instructions', None)
        project_knowledge_base_id = kwargs.pop('project_knowledge_base_id', None)
        project_default_model = kwargs.pop('project_default_model', None)
        project_name = kwargs.pop('project_name', None)
        project_file_names = kwargs.pop('project_file_names', None)

        # Resolve all provider keys in one call
        api_keys = {}
        if tenant_id:
            api_keys = await resolve_all_llm_keys(db, UUID(str(tenant_id)))

        # Fall back to legacy per-user keys if no tenant keys found
        if not api_keys:
            for provider in ["openrouter", "openai", "anthropic"]:
                key = await self.get_api_key(provider, db, user_id)
                if key:
                    api_keys[provider] = key

        # Load tool-level keys (search providers, etc.) from api_keys table
        if tenant_id:
            await self._load_tool_api_keys(db, tenant_id, api_keys)

        agent_id = kwargs.pop('agent_id', None)

        # Create dependencies
        deps = AgentDependencies(
            db=db,
            user_id=user_id,
            tenant_id=tenant_id,
            api_keys=api_keys,
            session_id=session_id,
            connectors=connectors or {},
            agent_id=agent_id,
            specific_sites=specific_sites,
            project_instructions=project_instructions,
            project_knowledge_base_id=project_knowledge_base_id,
            project_default_model=project_default_model,
            project_name=project_name,
            project_file_names=project_file_names,
        )

        # Build context with full memory stack (recitation + preferences + learnings + episodic + RAG + project)
        enhanced_message = await self._build_context_with_memory(
            user_message, session_id, user_id, tenant_id, db,
            agent_id=agent_id,
            active_knowledge_base_ids=active_knowledge_base_ids,
            project_instructions=project_instructions,
            project_knowledge_base_id=project_knowledge_base_id,
            project_name=project_name,
            project_file_names=project_file_names,
        )

        # Resolve effective model + API key (override > project > tenant > agent default)
        effective_model, api_key, provider_type = await resolve_model_and_key(
            db=db,
            tenant_id=UUID(str(tenant_id)) if tenant_id else None,
            api_keys=api_keys,
            model_override=model_override,
            project_default_model=project_default_model,
            agent_default_model=self.model,
        )

        logger.info(f"Agent streaming with model={effective_model!r} (override={model_override!r}, provider={provider_type!r})")

        # Wrap extra tools with harness (loop detection + credential scanning + recitation).
        if extra_tools and session_id and get_settings().USE_HARNESS_PIPELINE:
            from app.agents.harness import wrap_tools_with_harness
            extra_tools = wrap_tools_with_harness(
                extra_tools, session_id, str(tenant_id) if tenant_id else "",
            )

        # Build per-invocation toolset for extra tools (skills/connectors).
        # This is additive — agent's static @tool decorators remain available.
        # Sort by name for deterministic tool ordering — maximises KV-cache reuse.
        request_toolset = FunctionToolset()
        if extra_tools:
            extra_tools = sorted(
                extra_tools,
                key=lambda f: getattr(f, '__name__', None) or getattr(f, '__skill_name__', 'unknown'),
            )
            from pydantic_ai.tools import Tool as PydanticTool
            for func in extra_tools:
                if isinstance(func, PydanticTool):
                    # Already a Tool object (e.g., workflow tools) — add directly
                    request_toolset.tools[func.name] = func
                else:
                    tool_name = getattr(func, '__name__', None) or getattr(func, '__skill_name__', 'unknown')
                    func = self._wrap_tool_with_observability(func, tool_name)
                    tool = PydanticTool(func, takes_ctx=True, max_retries=self._agent_retries)
                    request_toolset.tools[tool.name] = tool

        run_toolsets = [request_toolset] if request_toolset.tools else []

        # Enforce quota before LLM call (same check as run())
        if tenant_id:
            await self._check_quota(db, tenant_id, user_id, enhanced_message)

        # Create LiteLLM-backed model with resolved key
        model = create_litellm_model(effective_model, api_key)

        # Run agent with streaming and dynamic model, passing per-invocation toolset
        async with self.agent.run_stream(
            enhanced_message, deps=deps, model=model,
            toolsets=run_toolsets or None,
            **kwargs,
        ) as result:
            yield result

    # =========================================================================
    # Simplified Storage Pattern Methods
    # =========================================================================

    async def mount_volume(
        self,
        volume_name: str,
        session_id: str,
        tenant_id: Optional[Union[str, UUID]] = None,
    ) -> Optional[str]:
        """
        Mount a volume to the current session.

        Volumes are pre-warmed input data that agents can read from.
        Use this to provide agents with market data, SEC filings, etc.

        Args:
            volume_name: Name of the volume to mount
            session_id: Session to mount the volume to
            tenant_id: Tenant ID for multi-tenant isolation (falls back to self.deps.tenant_id)

        Returns:
            Path to mounted volume, or None if mounting disabled or failed
        """
        settings = get_settings()

        if not getattr(settings, 'USE_VOLUMES', False):
            return None

        resolved_tenant_id = str(tenant_id) if tenant_id else "unknown"

        try:
            from app.services.volumes import get_volume_manager
            volume_mgr = get_volume_manager()
            mount_path = await volume_mgr.mount_volume(resolved_tenant_id, volume_name, session_id)
            return str(mount_path)
        except Exception as e:
            logger.warning(f"Failed to mount volume {volume_name}: {e}")
            return None

    async def save_artifact(
        self,
        session_id: str,
        file_path: str,
        content: bytes,
        metadata: Optional[Dict[str, Any]] = None,
        content_type: Optional[str] = None,
        tenant_id: Optional[Union[str, UUID]] = None,
    ) -> Optional[str]:
        """
        Save an artifact to persistent storage.

        Artifacts are outputs that need to be retained (7 years for compliance).
        Use this to save reports, analysis results, compliance audits, etc.

        Args:
            session_id: Session the artifact belongs to
            file_path: Relative path for the artifact (e.g., "report.pdf")
            content: Binary content to save
            metadata: Optional metadata (user_id, agent_id, etc.)
            content_type: MIME type of the content
            tenant_id: Tenant ID for multi-tenant isolation

        Returns:
            Storage URI (s3:// or file://), or None if artifacts disabled
        """
        settings = get_settings()

        if not getattr(settings, 'USE_ARTIFACTS', False):
            return None

        resolved_tenant_id = str(tenant_id) if tenant_id else "unknown"

        try:
            from app.services.artifacts import get_artifact_storage
            artifact_storage = get_artifact_storage()

            # Add agent info to metadata
            full_metadata = {
                "agent_name": self.name,
                **(metadata or {}),
            }

            return await artifact_storage.write(
                tenant_id=resolved_tenant_id,
                session_id=session_id,
                file_path=file_path,
                content=content,
                metadata=full_metadata,
                content_type=content_type,
            )
        except Exception as e:
            logger.warning(f"Failed to save artifact {file_path}: {e}")
            return None

    async def log_to_session(
        self,
        session_id: str,
        action: str,
        details: Optional[str] = None,
        tenant_id: Optional[Union[str, UUID]] = None,
    ) -> None:
        """
        Log an action to the session's history.

        Useful for audit trails and debugging long-running sessions.

        Args:
            session_id: Session to log to
            action: Short action description
            details: Optional detailed description
            tenant_id: Tenant ID for multi-tenant isolation
        """
        settings = get_settings()

        if not getattr(settings, 'USE_SESSION_FILESYSTEM', False):
            return

        resolved_tenant_id = str(tenant_id) if tenant_id else "unknown"

        try:
            from app.services.session_storage import get_session_storage
            from datetime import datetime
            storage = get_session_storage()
            timestamp = datetime.utcnow().isoformat()
            entry = f"\n## {timestamp}\n\n**Action:** {action}\n"
            if details:
                entry += f"\n{details}\n"
            agent_id = getattr(self, 'deps', None) and getattr(self.deps, 'agent_id', None)
            await storage.append(
                resolved_tenant_id, session_id, "logs/history.md", entry,
                agent_id=agent_id,
            )
        except Exception:
            pass  # Don't fail agent execution on logging errors

    async def update_session_state(
        self,
        session_id: str,
        updates: Dict[str, Any],
        tenant_id: Optional[Union[str, UUID]] = None,
    ) -> None:
        """
        Update the session's state.json file.

        Args:
            session_id: Session to update
            updates: Key-value pairs to merge into state
            tenant_id: Tenant ID for multi-tenant isolation
        """
        settings = get_settings()

        if not getattr(settings, 'USE_SESSION_FILESYSTEM', False):
            return

        resolved_tenant_id = str(tenant_id) if tenant_id else "unknown"

        try:
            from app.services.session_storage import get_session_storage
            import json as _json
            from datetime import datetime
            storage = get_session_storage()
            agent_id = getattr(self, 'deps', None) and getattr(self.deps, 'agent_id', None)
            state_content = await storage.read(
                resolved_tenant_id, session_id, "state.json", agent_id=agent_id,
            )
            state = _json.loads(state_content) if state_content else {}
            state.update(updates)
            state["updated_at"] = datetime.utcnow().isoformat()
            await storage.write(
                resolved_tenant_id, session_id, "state.json",
                _json.dumps(state, indent=2), agent_id=agent_id,
            )
        except Exception:
            pass  # Don't fail agent execution on state update errors
