"""
Agent Resolver — shared functions for resolving custom agents.

Extracted from chat.py so both chat.py and agent_chat.py can use them.
"""

import json
import uuid
import logging
from typing import Optional, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, set_tenant_context
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


async def _resolve_production_version_id(
    agent_id: uuid.UUID,
    db: Optional[AsyncSession] = None,
) -> Optional[uuid.UUID]:
    """Look up the PRODUCTION alias for an agent and return the version_id it points to."""
    from app.models.agent_alias import AgentAlias

    async def _lookup(session: AsyncSession):
        result = await session.execute(
            select(AgentAlias.version_id).where(
                AgentAlias.agent_id == agent_id,
                AgentAlias.alias_name == 'PRODUCTION'
            )
        )
        return result.scalar_one_or_none()

    if db is not None:
        return await _lookup(db)
    else:
        async with AsyncSessionLocal() as _db:
            return await _lookup(_db)


def build_message_history(messages: list[dict], include_last: bool = False) -> list | None:
    """Build Pydantic AI message_history from prior conversation turns.

    If assistant messages contain metadata.parts (tool call data from
    enhanced SSE stream), reconstruct ToolCallPart/ToolReturnPart
    so the LLM has full tool context on followup turns.

    Args:
        messages: List of message dicts with role, content, and optional metadata.
        include_last: If True, include all messages (for DB-loaded history).
                      If False (default), exclude the last message (current user msg).
    """
    from pydantic_ai.messages import (
        ModelRequest, ModelResponse,
        UserPromptPart, TextPart as AiTextPart,
        ToolCallPart, ToolReturnPart,
    )
    from app.agents.patch_tool_calls import patch_dangling_tool_calls

    history: list = []
    prior = messages if include_last else (messages[:-1] if messages else [])

    for i, msg in enumerate(prior):
        role = msg.get("role", "")
        metadata = msg.get("metadata") or {}
        parts_data = metadata.get("parts") or []

        if role == "user":
            content = _extract_text(msg)
            if content:
                history.append(ModelRequest(parts=[UserPromptPart(content=content)]))

        elif role == "assistant":
            tool_calls = [
                p for p in parts_data
                if p.get("type") == "tool_call" and p.get("status") in ("success", "error")
            ]

            if tool_calls:
                # Build ModelResponse with ToolCallParts (+ optional TextPart)
                response_parts = []
                return_parts = []

                for j, tc in enumerate(tool_calls):
                    tc_id = tc.get("id") or f"tc-reconstructed-{i}-{j}"
                    tool_name = tc.get("toolName", "unknown")

                    response_parts.append(ToolCallPart(
                        tool_name=tool_name,
                        args=tc.get("args") or {},
                        tool_call_id=tc_id,
                    ))

                    # Reconstruct result — prefer result, fall back to error
                    if tc.get("status") == "error":
                        result_content = f"[Error: {tc.get('error', 'unknown error')}]"
                    else:
                        raw = tc.get("result")
                        if raw is None:
                            result_content = "[Result too large to store — see assistant response below for summary]"
                        elif isinstance(raw, dict):
                            result_content = json.dumps(raw, default=str)
                        else:
                            result_content = str(raw)

                    return_parts.append(ToolReturnPart(
                        tool_name=tool_name,
                        tool_call_id=tc_id,
                        content=result_content[:4000],
                    ))

                # Include text in the same ModelResponse if present
                content = _extract_text(msg)
                if content:
                    response_parts.append(AiTextPart(content=content))

                history.append(ModelResponse(parts=response_parts))
                history.append(ModelRequest(parts=return_parts))
            else:
                # No tool calls — text-only response
                content = _extract_text(msg)
                if content:
                    history.append(ModelResponse(parts=[AiTextPart(content=content)]))

    if not history:
        return None
    return patch_dangling_tool_calls(history)


def _extract_text(msg: dict) -> str:
    """Extract text content from a message dict."""
    if "parts" in msg:
        text_parts = [p["text"] for p in msg["parts"] if p.get("type") == "text"]
        return " ".join(text_parts) if text_parts else ""
    if "content" in msg:
        content = msg["content"]
        return content if isinstance(content, str) else str(content)
    return ""


async def resolve_custom_agent(
    custom_agent,
    version_id: Optional[uuid.UUID] = None,
    db: Optional[AsyncSession] = None,
    use_production_alias: bool = False,
):
    """
    Resolve a CustomAgent ORM object into a BaseAgent with tools and config.

    Accepts the ORM object directly (already loaded by verify_agent_access).

    Args:
        custom_agent: CustomAgent ORM object
        version_id: Optional version to load config from AgentVersion.config JSONB
        db: Optional database session
        use_production_alias: If True and no version_id, resolve from PRODUCTION alias

    Returns:
        tuple: (agent, tools, connector_ids, knowledge_base_ids, model, workflow_ids)
    """
    from app.agents.base import BaseAgent
    from app.agents.skills.registry import SkillRegistry

    # If no explicit version_id and use_production_alias, look up PRODUCTION alias
    if not version_id and use_production_alias:
        version_id = await _resolve_production_version_id(custom_agent.id, db)

    # Start with the agent's current config
    system_prompt = custom_agent.system_prompt or "You are a helpful AI assistant."
    model = custom_agent.model or "gpt-4o"
    temperature = custom_agent.temperature
    max_tokens = custom_agent.max_tokens
    skill_names = list(custom_agent.skills or [])
    connector_ids = [str(cid) for cid in (custom_agent.connector_ids or [])]
    knowledge_base_ids = [str(kid) for kid in (custom_agent.knowledge_base_ids or [])]
    workflow_ids = [str(wid) for wid in (custom_agent.workflow_ids or [])]

    # Override with version config if specified (from explicit version_id or PRODUCTION alias)
    if version_id:
        async def _load_version(session: AsyncSession):
            from app.models.agent_version import AgentVersion
            result = await session.execute(
                select(AgentVersion).where(
                    AgentVersion.id == version_id,
                    AgentVersion.agent_id == custom_agent.id,
                )
            )
            return result.scalar_one_or_none()

        if db is not None:
            version = await _load_version(db)
        else:
            async with AsyncSessionLocal() as _db:
                version = await _load_version(_db)

        if version and version.config:
            from app.schemas.agent_studio import AgentVersionConfig
            try:
                config = AgentVersionConfig.model_validate(version.config)
                if config.system_prompt is not None:
                    system_prompt = config.system_prompt
                if config.model is not None:
                    model = config.model
                if config.temperature is not None:
                    temperature = config.temperature
                if config.max_tokens is not None:
                    max_tokens = config.max_tokens
                if config.skills is not None:
                    skill_names = config.skills
                if config.connector_ids is not None:
                    connector_ids = config.connector_ids
                if config.knowledge_base_ids is not None:
                    knowledge_base_ids = config.knowledge_base_ids
                if config.workflow_ids is not None:
                    workflow_ids = config.workflow_ids
            except Exception as e:
                logger.warning(f"Invalid version config for {version_id}: {e}")

    # Build BaseAgent
    agent_kwargs = {}
    if temperature is not None:
        agent_kwargs['temperature'] = temperature
    if max_tokens is not None:
        agent_kwargs['max_tokens'] = max_tokens

    agent = BaseAgent(
        name=custom_agent.name or "custom-agent",
        description=custom_agent.description or "Custom AI agent",
        model=model,
        system_prompt=system_prompt,
        output_type=str,
        **agent_kwargs,
    )

    # Load skill handlers
    tools = []
    for skill_name in skill_names:
        loaded = SkillRegistry.get_skill(skill_name)
        if loaded and loaded.handler:
            tools.append(loaded.handler)

    return agent, tools, connector_ids, knowledge_base_ids, model, workflow_ids


async def wire_skills_and_connectors(
    agent,
    skill_ids: Optional[List[str]],
    connector_ids: Optional[List[str]],
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    user_session_id: str = "",
    db: Optional[AsyncSession] = None,
    workflow_ids: Optional[List[str]] = None,
) -> tuple:
    """
    Load skill handlers, connector context, and workflow tools for a chat request.

    Returns:
        (extra_tools, connectors_dict)
    """
    from app.agents.skills.registry import SkillRegistry

    extra_tools = []
    connectors_dict = {}

    # --- Skills: load handlers from registry (filtered by tenant features) ---
    if skill_ids:
        from uuid import uuid5, NAMESPACE_DNS
        _ns = uuid5(NAMESPACE_DNS, "system.skills.oniva")

        # Filter skills by tenant feature flags when possible
        tenant_obj = None
        if tenant_id and db:
            try:
                from app.models.tenant import Tenant as TenantModel
                _tenant_result = await db.execute(
                    select(TenantModel).where(TenantModel.id == tenant_id)
                )
                tenant_obj = _tenant_result.scalar_one_or_none()
            except Exception:
                pass  # Fall back to unfiltered on error

        if tenant_obj:
            all_manifests = SkillRegistry.get_skills_for_tenant(tenant_obj)
        else:
            all_manifests = SkillRegistry.get_all_skills()
        _uuid_to_name = {str(uuid5(_ns, n)): n for n in all_manifests}

        for skill_ref in skill_ids:
            skill_name = _uuid_to_name.get(skill_ref, skill_ref)
            loaded = SkillRegistry.get_skill(skill_name)
            if loaded and loaded.handler:
                extra_tools.append(loaded.handler)

    # --- Connectors: build dict for AgentDependencies ---
    if connector_ids:
        sandbox_wired_providers: set[str] = set()

        async def _load_connectors(session: AsyncSession):
            if tenant_id:
                await set_tenant_context(session, str(tenant_id))
            from app.models.connector import Connector
            results = await session.execute(
                select(Connector).where(
                    Connector.id.in_([uuid.UUID(cid) for cid in connector_ids]),
                    Connector.tenant_id == tenant_id,
                    Connector.is_active == True,
                )
            )
            return results.scalars().all()

        if db is not None:
            connector_rows = await _load_connectors(db)
        else:
            async with AsyncSessionLocal() as _db:
                connector_rows = await _load_connectors(_db)

        for row in connector_rows:
            connectors_dict[str(row.id)] = {
                "provider": row.provider,
                "name": row.name,
                "type": row.connector_type,
            }

        # --- Sandbox Connector tools for OpenAPI connectors ---
        if (
            settings.USE_SANDBOX_CONNECTORS
            and settings.ENABLE_SANDBOX
            and user_id
        ):
            from app.connectors.openapi.sandbox_connector import SandboxConnector
            from app.connectors.openapi.pydantic_ai_bridge import build_pydantic_tools
            from app.connectors.registry import _resolve_connector_credentials

            for row in connector_rows:
                if row.connector_type != "openapi" or not row.openapi_operations:
                    continue

                try:
                    connector = SandboxConnector.from_db_connector(
                        row, row.config or {}
                    )
                    credentials = await _resolve_connector_credentials(
                        connector_id=row.provider,
                        tenant_id=str(tenant_id),
                        user_id=str(user_id),
                    )
                    sandbox_tools = build_pydantic_tools(
                        connector=connector,
                        tenant_id=str(tenant_id),
                        user_id=str(user_id),
                        user_session_id=user_session_id,
                        credentials=credentials,
                    )
                    extra_tools.extend(sandbox_tools)
                    sandbox_wired_providers.add(row.provider)
                    logger.info(
                        "Wired sandbox tools for connector %s (%s): %d tools",
                        row.name, row.provider, len(sandbox_tools),
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to wire sandbox tools for %s: %s",
                        row.provider, e,
                    )

        # Inject generic connector skills only if we have non-sandbox connectors
        has_non_sandbox = any(
            info.get("provider") not in sandbox_wired_providers
            for info in connectors_dict.values()
        )
        if has_non_sandbox or not sandbox_wired_providers:
            existing_tool_names = {
                getattr(t, '__name__', '') for t in getattr(agent, '_extra_tools', [])
            }
            for connector_skill in [
                "list_available_connectors",
                "invoke_connector_tool",
                "get_connector_schema",
            ]:
                if connector_skill not in existing_tool_names:
                    loaded = SkillRegistry.get_skill(connector_skill)
                    if loaded and loaded.handler:
                        extra_tools.append(loaded.handler)

    # --- Workflows: load from DB and generate tools ---
    if workflow_ids:
        if len(workflow_ids) > 10:
            logger.warning(
                "Agent has %d attached workflows (>10) — performance may degrade",
                len(workflow_ids),
            )

        from app.agents.skills.workflow_tool_generator import generate_workflow_tools
        from app.models.workflow import Workflow

        async def _load_workflows(session: AsyncSession):
            if tenant_id:
                await set_tenant_context(session, str(tenant_id))
            result = await session.execute(
                select(Workflow).where(
                    Workflow.id.in_([uuid.UUID(wid) for wid in workflow_ids]),
                    Workflow.tenant_id == tenant_id,
                )
            )
            return result.scalars().all()

        if db is not None:
            workflow_rows = await _load_workflows(db)
        else:
            async with AsyncSessionLocal() as _db:
                workflow_rows = await _load_workflows(_db)

        loaded_ids = {str(w.id) for w in workflow_rows}
        missing_ids = set(workflow_ids) - loaded_ids
        if missing_ids:
            logger.warning(
                "Skipping %d missing/deleted workflow(s): %s",
                len(missing_ids), missing_ids,
            )

        if workflow_rows:
            workflow_tools = generate_workflow_tools(workflow_rows)
            extra_tools.extend(workflow_tools)
            logger.info(
                "Wired %d workflow tool(s) from %d workflow(s)",
                len(workflow_tools), len(workflow_rows),
            )

    return extra_tools, connectors_dict
