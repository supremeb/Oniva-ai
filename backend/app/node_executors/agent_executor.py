"""
Agent Node Executor
Executes custom configured agents with their skills and connectors

Enhanced with Vellum-inspired auto tool loop:
- Agents automatically call tools in a loop until complete
- Configurable max iterations to prevent runaway execution
- Full tool call tracking for debugging and audit
- Intermediate reasoning exposed in output
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.messages import ModelMessage
from app.agents.patch_tool_calls import patch_dangling_tool_calls
from typing import Optional, Dict, Any, List
from uuid import UUID
import litellm
import os
import time
import logging

from app.models.custom_agent import CustomAgent
from app.models.connector import Connector
from app.agents.skills.registry import SkillRegistry
from app.config import get_settings
from app.api.dependencies import BUILTIN_AGENTS
from app.core.builtin_agents import is_builtin_agent_id, uuid_to_slug
from app.services.agent_circuit_breaker import AgentCircuitBreaker
from app.services.redis_ha import get_redis_service
from app.services.llm_gateway import (
    resolve_key_with_fallback, normalize_model_id, get_provider_from_model,
    create_litellm_model,
)

logger = logging.getLogger(__name__)


class ToolCallRecord(BaseModel):
    """Record of a single tool call during agent execution"""
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: Optional[str] = None
    duration_ms: int = 0


class AgentLoopIteration(BaseModel):
    """Record of a single iteration in the agent's tool loop"""
    iteration: int
    thought: Optional[str] = None  # Agent's reasoning before tool call
    tool_calls: List[ToolCallRecord] = Field(default_factory=list)
    response: Optional[str] = None  # Partial response if any
    duration_ms: int = 0


class AgentOutput(BaseModel):
    """Output schema for Agent node with enhanced tool loop tracking"""
    response: str
    agent_name: str
    model: str
    skills_used: list[str]
    execution_time: float
    # Enhanced tool loop tracking
    iterations: List[AgentLoopIteration] = Field(default_factory=list)
    total_iterations: int = 1
    total_tool_calls: int = 0
    reasoning: Optional[str] = None  # Combined reasoning/thought process
    stopped_reason: str = "complete"  # complete, max_iterations, error, no_tool_calls


class AgentExecutor(BaseNodeExecutor):
    """
    Executor for Agent node - runs custom configured agents

    Enhanced with Vellum-inspired auto tool loop:
    - Automatically calls tools in a loop until the agent indicates completion

    Phase 1 Security: Agents wrap LLM calls which call external APIs
    """

    # Agent calls external LLM APIs
    makes_external_calls = True

    """
    - Configurable max iterations (default: 10) to prevent runaway execution
    - Full tracking of tool calls, reasoning, and intermediate results
    - Supports both single-turn and multi-turn agent interactions
    """

    node_type = "agent"

    # Default max iterations for tool loop
    DEFAULT_MAX_ITERATIONS = 10

    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        """
        Execute agent with auto tool loop

        The agent will automatically call tools in a loop until:
        1. It provides a final response without tool calls
        2. Max iterations are reached
        3. An error occurs
        """
        start_time = time.time()

        try:
            node_data = context.node_data

            # Get agent configuration
            agent_id_str = node_data.get("agentId")
            if not agent_id_str:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="error",
                    error="Agent ID not specified",
                    execution_time=time.time() - start_time
                )

            # Get max iterations from node config or use default
            max_iterations = node_data.get("maxIterations", self.DEFAULT_MAX_ITERATIONS)
            enable_tool_loop = node_data.get("enableToolLoop", True)

            # Check if this is a built-in agent (accepts both slugs and UUIDs)
            if is_builtin_agent_id(agent_id_str):
                agent_slug = uuid_to_slug(agent_id_str)
                return await self._execute_builtin_agent(
                    context=context,
                    agent_type=agent_slug,
                    node_data=node_data,
                    db=db,
                    start_time=start_time
                )

            # Try to parse as UUID for custom agent
            try:
                agent_id = UUID(agent_id_str)

                # Circuit breaker check for custom agents
                redis = await get_redis_service()
                breaker = AgentCircuitBreaker(redis)
                await breaker.check_health(agent_id)  # Raises CircuitBreakerOpenError if degraded

            except ValueError:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="error",
                    error=f"Invalid agent ID: {agent_id_str}. Expected UUID or one of: {list(BUILTIN_AGENTS.keys())}",
                    execution_time=time.time() - start_time
                )

            # Get custom agent from database
            stmt = select(CustomAgent).where(CustomAgent.id == agent_id)
            result = await db.execute(stmt)
            custom_agent = result.scalar_one_or_none()

            if not custom_agent:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="error",
                    error=f"Agent not found: {agent_id}",
                    execution_time=time.time() - start_time
                )

            if not custom_agent.is_active:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="error",
                    error=f"Agent '{custom_agent.name}' is not active",
                    execution_time=time.time() - start_time
                )

            # Get user prompt with variable substitution
            user_prompt = node_data.get("userPrompt", "")
            user_prompt = self.substitute_variables(
                user_prompt,
                context.variables,
                context.inputs
            )

            if not user_prompt:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="error",
                    error="User prompt not specified",
                    execution_time=time.time() - start_time
                )

            # Get model configuration via unified gateway
            model_id = normalize_model_id(custom_agent.model or "openai/gpt-4o-mini")

            # Resolve API key via unified gateway (with OpenRouter fallback)
            tenant_uuid = UUID(str(custom_agent.tenant_id)) if custom_agent.tenant_id else None
            api_key, model_id = await resolve_key_with_fallback(db, tenant_uuid, model_id)

            if not api_key:
                provider_type = get_provider_from_model(model_id)
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="error",
                    error=f"No API key found for provider: {provider_type}. Add your key in Settings > LLM Providers.",
                    execution_time=time.time() - start_time
                )

            # Create LiteLLM-backed pydantic-ai model (no os.environ mutation)
            pydantic_model = create_litellm_model(model_id, api_key)

            # Load connector data for agents that have connector_ids
            connectors_dict = {}
            if custom_agent.connector_ids:
                from app.core.encryption import get_encryption_service
                encryption = get_encryption_service()
                for conn_uuid in custom_agent.connector_ids:
                    conn_stmt = select(Connector).where(
                        Connector.id == conn_uuid,
                        Connector.is_active == True,
                    )
                    conn_result = await db.execute(conn_stmt)
                    conn = conn_result.scalar_one_or_none()
                    if conn:
                        credentials = {}
                        if conn.encrypted_credentials:
                            try:
                                import json
                                credentials = json.loads(encryption.decrypt(conn.encrypted_credentials))
                            except Exception:
                                pass
                        connectors_dict[str(conn.id)] = {
                            "name": conn.name,
                            "type": conn.connector_type,
                            "provider": conn.provider,
                            "is_verified": conn.is_verified,
                            "config": conn.config or {},
                            "credentials": credentials,
                        }

            # Load tool API keys (Tavily, Exa, etc.) for skill execution
            tool_api_keys = await self._load_tool_api_keys(
                db=db,
                tenant_id=custom_agent.tenant_id,
            )

            # Build agent dependencies for skill context
            from app.agents.base import AgentDependencies
            agent_deps = AgentDependencies(
                db=db,
                user_id=str(custom_agent.user_id),
                tenant_id=str(custom_agent.tenant_id) if custom_agent.tenant_id else None,
                connectors=connectors_dict,
                api_keys=tool_api_keys,
            )

            # Get skills as tools
            tools = []
            skill_names = []
            tool_handlers = {}  # Map tool name to handler for tracking

            if custom_agent.skills:
                for skill_name in custom_agent.skills:
                    skill = SkillRegistry.get_skill(skill_name)
                    if skill and skill.enabled:
                        tools.append(skill.handler)
                        skill_names.append(skill_name)
                        tool_handlers[skill_name] = skill.handler

            # Create pydantic-ai agent with tool loop enabled
            system_prompt = custom_agent.system_prompt or "You are a helpful AI assistant."

            # Prepend relevant learnings from past experience
            settings = get_settings()
            if getattr(settings, 'USE_AGENT_LEARNING', False):
                try:
                    from app.services.agent_learning import get_agent_learning_service
                    learning_service = get_agent_learning_service()
                    learnings = await learning_service.search_relevant_learnings(
                        db=db,
                        tenant_id=custom_agent.tenant_id,
                        query=user_prompt,
                        agent_id=agent_id,
                        limit=3,
                        min_similarity=0.6,
                    )
                    if learnings:
                        learning_context = learning_service.format_for_context(learnings)
                        system_prompt = learning_context + "\n\n" + system_prompt
                except Exception as e:
                    logger.warning(f"AgentExecutor learning retrieval failed: {e}")

            # Enhance system prompt for tool loop behavior
            if enable_tool_loop and tools:
                system_prompt += (
                    "\n\nYou have access to tools. Use them when needed to accomplish the task. "
                    "Continue using tools until you have all the information needed to provide "
                    "a complete response."
                )

            pydantic_agent = PydanticAgent(
                model=pydantic_model,
                system_prompt=system_prompt,
                deps_type=AgentDependencies,
                retries=2
            )

            # Add tools if available
            if tools:
                for tool in tools:
                    pydantic_agent.tool(tool)

            # Execute with auto tool loop
            iterations: List[AgentLoopIteration] = []
            total_tool_calls = 0
            all_thoughts: List[str] = []
            stopped_reason = "complete"
            final_response = ""

            if enable_tool_loop and tools:
                # Multi-iteration tool loop
                message_history: List[ModelMessage] = []
                current_prompt = user_prompt

                # Get model context window for truncation budget
                try:
                    _model_info = litellm.get_model_info(model) if model else {}
                except Exception:
                    _model_info = {}
                _max_input_tokens = _model_info.get("max_input_tokens", 128_000)
                _truncation_threshold = int(_max_input_tokens * 0.7)
                _truncation_target = int(_max_input_tokens * 0.5)

                for iteration_num in range(1, max_iterations + 1):
                    iteration_start = time.time()
                    iteration_record = AgentLoopIteration(
                        iteration=iteration_num,
                        tool_calls=[],
                        duration_ms=0
                    )

                    # Truncate message_history if context exceeds 70% of model window
                    # Reuses the len//4 estimation pattern from base.py:593
                    if message_history:
                        _estimated_tokens = sum(len(str(m)) // 4 for m in message_history)
                        if _estimated_tokens > _truncation_threshold:
                            _kept = []
                            _running = 0
                            for _msg in reversed(message_history):
                                _msg_tokens = len(str(_msg)) // 4
                                if _running + _msg_tokens > _truncation_target:
                                    break
                                _kept.insert(0, _msg)
                                _running += _msg_tokens
                            logger.info(
                                f"Truncated message_history: {_estimated_tokens} → {_running} tokens "
                                f"(iteration {iteration_num})"
                            )
                            message_history = _kept

                    try:
                        # Run the agent for this iteration
                        result = await pydantic_agent.run(
                            current_prompt,
                            deps=agent_deps,
                            message_history=message_history if message_history else None
                        )

                        # Extract response
                        response_text = result.output if hasattr(result, 'output') else str(result)
                        iteration_record.response = response_text

                        # Track tool calls from this iteration if available
                        if hasattr(result, '_all_messages'):
                            for msg in result._all_messages:
                                # Check for tool calls in messages
                                if hasattr(msg, 'parts'):
                                    for part in msg.parts:
                                        if hasattr(part, 'tool_name'):
                                            tool_record = ToolCallRecord(
                                                tool_name=part.tool_name,
                                                arguments=getattr(part, 'args', {}),
                                                result=getattr(part, 'content', None),
                                                duration_ms=0
                                            )
                                            iteration_record.tool_calls.append(tool_record)
                                            total_tool_calls += 1

                        # Check if agent made tool calls this iteration
                        made_tool_calls = len(iteration_record.tool_calls) > 0

                        iteration_record.duration_ms = int((time.time() - iteration_start) * 1000)
                        iterations.append(iteration_record)

                        # Update message history for next iteration
                        if hasattr(result, '_all_messages'):
                            message_history = patch_dangling_tool_calls(list(result._all_messages))

                        # If no tool calls were made, agent is done
                        if not made_tool_calls:
                            final_response = response_text
                            stopped_reason = "complete"
                            logger.info(
                                f"Agent {custom_agent.name} completed after {iteration_num} iterations"
                            )
                            break

                        # If we're at max iterations, stop with the current response
                        if iteration_num >= max_iterations:
                            final_response = response_text
                            stopped_reason = "max_iterations"
                            logger.warning(
                                f"Agent {custom_agent.name} hit max iterations ({max_iterations})"
                            )
                            break

                        # Continue loop - agent made tool calls, expects to continue
                        current_prompt = "Continue with the task based on the tool results."

                    except Exception as e:
                        iteration_record.duration_ms = int((time.time() - iteration_start) * 1000)
                        iterations.append(iteration_record)
                        stopped_reason = "error"
                        logger.error(f"Agent iteration {iteration_num} failed: {e}")

                        # Capture error for learning system (fire-and-forget)
                        if getattr(settings, 'USE_AGENT_LEARNING', False):
                            try:
                                from app.services.agent_learning import get_agent_learning_service
                                await get_agent_learning_service().capture_from_error(
                                    tenant_id=custom_agent.tenant_id,
                                    error=e,
                                    context={"tool_name": "agent_loop", "iteration": iteration_num, "agent": custom_agent.name, "prompt": user_prompt[:200]},
                                    fix_applied="Execution failed - needs investigation",
                                    agent_id=agent_id,
                                )
                            except Exception:
                                pass  # Learning capture must never block execution

                        return NodeExecutionResult(
                            node_id=context.node_id,
                            status="error",
                            error=f"Agent execution failed at iteration {iteration_num}: {str(e)}",
                            execution_time=time.time() - start_time
                        )

            else:
                # Single-shot execution (no tool loop)
                try:
                    result = await pydantic_agent.run(user_prompt, deps=agent_deps)
                    final_response = result.output if hasattr(result, 'output') else str(result)
                    stopped_reason = "no_tool_calls" if not tools else "complete"
                except Exception as e:
                    # Capture error for learning system (fire-and-forget)
                    if getattr(settings, 'USE_AGENT_LEARNING', False):
                        try:
                            from app.services.agent_learning import get_agent_learning_service
                            await get_agent_learning_service().capture_from_error(
                                tenant_id=custom_agent.tenant_id,
                                error=e,
                                context={"tool_name": "agent_single_shot", "agent": custom_agent.name, "prompt": user_prompt[:200]},
                                fix_applied="Single-shot execution failed - needs investigation",
                                agent_id=agent_id,
                            )
                        except Exception:
                            pass  # Learning capture must never block execution

                    return NodeExecutionResult(
                        node_id=context.node_id,
                        status="error",
                        error=f"Agent execution failed: {str(e)}",
                        execution_time=time.time() - start_time
                    )

            execution_time = time.time() - start_time

            # Update agent usage stats
            custom_agent.usage_count += 1
            try:
                from datetime import datetime
                custom_agent.last_used_at = datetime.utcnow()
            except Exception:
                pass  # Ignore if column doesn't exist
            await db.commit()

            # Combine all reasoning/thoughts
            combined_reasoning = "\n".join(all_thoughts) if all_thoughts else None

            # Prepare output with full tool loop tracking
            output = AgentOutput(
                response=final_response,
                agent_name=custom_agent.name,
                model=model_id,
                skills_used=skill_names,
                execution_time=execution_time,
                iterations=iterations,
                total_iterations=len(iterations) or 1,
                total_tool_calls=total_tool_calls,
                reasoning=combined_reasoning,
                stopped_reason=stopped_reason
            )

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output=output.model_dump(),
                execution_time=execution_time,
                reasoning=combined_reasoning
            )

        except Exception as e:
            logger.exception(f"Unexpected error in agent execution: {e}")
            return NodeExecutionResult(
                node_id=context.node_id,
                status="error",
                error=f"Unexpected error in agent execution: {str(e)}",
                execution_time=time.time() - start_time
            )

    async def _load_tool_api_keys(
        self,
        db: AsyncSession,
        tenant_id,
    ) -> Dict[str, str]:
        """
        Load tool-level API keys (search providers, etc.) from the api_keys table.
        Mirrors BaseAgent._load_tool_api_keys pattern.
        """
        api_keys: Dict[str, str] = {}
        tool_providers = ("tavily", "parallelai", "exa", "serpapi", "e2b")
        try:
            from app.models.api_key import APIKey
            from app.core.encryption import get_encryption_service

            result = await db.execute(
                select(APIKey).where(
                    APIKey.tenant_id == UUID(str(tenant_id)),
                    APIKey.is_active == True,  # noqa: E712
                    APIKey.provider.in_(tool_providers),
                )
            )
            encryption = get_encryption_service()
            for row in result.scalars():
                api_keys[row.provider] = encryption.decrypt(row.encrypted_key)
        except Exception:
            logger.debug("AgentExecutor: failed to load tool keys from DB", exc_info=True)

        # Env var fallbacks
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

        return api_keys

    async def _execute_builtin_agent(
        self,
        context: NodeExecutionContext,
        agent_type: str,
        node_data: Dict[str, Any],
        db: AsyncSession,
        start_time: float
    ) -> NodeExecutionResult:
        """Execute a built-in agent (analysis, compliance, research, etc.)"""
        from app.agents.analysis import AnalysisAgent
        from app.agents.compliance import ComplianceAgent
        from app.agents.research import ResearchAgent
        from app.agents.data_retrieval import DataRetrievalAgent
        from app.agents.general import GeneralAgent

        try:
            # Get user prompt with variable substitution
            user_prompt = node_data.get("instruction", "")
            if not user_prompt:
                user_prompt = node_data.get("userPrompt", "")
            user_prompt = self.substitute_variables(
                user_prompt,
                context.variables,
                context.inputs
            )

            # Also try sys.query as input
            if not user_prompt and "sys.query" in context.variables:
                user_prompt = str(context.variables["sys.query"])

            if not user_prompt:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="error",
                    error="No prompt provided for built-in agent",
                    execution_time=time.time() - start_time
                )

            # Get API key for the model - prioritize environment variables for built-in agents
            # to avoid RLS context issues with db lookups during workflow execution
            api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                try:
                    api_key = await self._get_api_key("openai", db, context.user_id)
                except Exception as e:
                    logger.warning(f"Failed to get API key from database: {e}")

            # Create the appropriate agent based on type
            agent_info = BUILTIN_AGENTS[agent_type]
            agent_class_type = agent_info.get("type", agent_type)
            agent_name = agent_info.get("name", agent_type)
            agent_description = agent_info.get("description", "")
            default_system_prompt = f"You are {agent_name}. {agent_description}"

            # Most agents use no-args constructor with hardcoded settings
            # Only ComplianceAgent requires name, description, system_prompt
            if agent_class_type in ("analysis", "Analysis Agent"):
                agent = AnalysisAgent()
            elif agent_class_type in ("compliance", "Compliance Agent"):
                agent = ComplianceAgent(
                    name=agent_name,
                    description=agent_description,
                    system_prompt=default_system_prompt
                )
            elif agent_class_type in ("research", "Research Agent"):
                agent = ResearchAgent()
            elif agent_class_type in ("data", "data-retrieval", "Data Retrieval Agent"):
                agent = DataRetrievalAgent()
            else:
                agent = GeneralAgent()

            logger.info(f"Executing built-in agent: {agent_type} ({agent_class_type})")

            # Execute the agent with required db and user_id arguments
            result = await agent.run(user_prompt, db=db, user_id=context.user_id)

            # Extract response text from result - handle structured result types
            if hasattr(result, "output"):
                # RunResult from pydantic_ai
                data = result.output
                if hasattr(data, "model_dump"):
                    # Pydantic model - convert to JSON string
                    import json
                    response_text = json.dumps(data.model_dump(), indent=2)
                else:
                    response_text = str(data)
            else:
                response_text = str(result)

            execution_time = time.time() - start_time

            output = AgentOutput(
                response=response_text,
                agent_name=agent_info.get("name", agent_type),
                model="gpt-4o",  # Default model for built-in agents
                skills_used=[],
                execution_time=execution_time,
                iterations=[],
                total_iterations=1,
                total_tool_calls=0,
                reasoning=None,
                stopped_reason="complete"
            )

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output=output.model_dump(),
                execution_time=execution_time
            )

        except Exception as e:
            logger.exception(f"Error executing built-in agent {agent_type}: {e}")
            return NodeExecutionResult(
                node_id=context.node_id,
                status="error",
                error=f"Built-in agent execution failed: {str(e)}",
                execution_time=time.time() - start_time
            )
