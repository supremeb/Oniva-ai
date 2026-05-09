"""
LLM Node Executor
Executes LLM calls with model selection, prompts, timeout, and retry support

C2 Enhancement: State machine integration for tool safety.
When USE_STATE_MACHINE is enabled, agents are wrapped with StatefulAgent
to enforce tool access based on current workflow state.
"""

from app.node_executors import BaseNodeExecutor, NodeExecutionContext, NodeExecutionResult
from sqlalchemy.ext.asyncio import AsyncSession
import time
import asyncio
import logging

import litellm
from pydantic import BaseModel
from typing import Optional

from app.config import get_settings
from app.agents.state_machine import WorkflowState
from uuid import UUID
from app.services.llm_gateway import (
    resolve_key_with_fallback, normalize_model_id, get_provider_from_model,
)

logger = logging.getLogger(__name__)
settings = get_settings()

# Import DataClassification for type hints


def extract_thinking_from_result(result) -> Optional[str]:
    """
    Extract thinking/reasoning from LLM response.
    Supports Pydantic AI ThinkingPart and OpenAI reasoning models.
    """
    if not settings.USE_COT_REASONING:
        return None

    thinking_parts = []

    # Check for Pydantic AI ThinkingPart in messages
    if hasattr(result, 'all_messages'):
        try:
            for msg in result.all_messages():
                if hasattr(msg, 'parts'):
                    for part in msg.parts:
                        # Check for ThinkingPart by class name
                        if part.__class__.__name__ == 'ThinkingPart':
                            if hasattr(part, 'content') and part.content:
                                thinking_parts.append(part.content)
        except Exception as e:
            logger.debug(f"Error extracting thinking from messages: {e}")

    # Check for direct _reasoning attribute
    if hasattr(result, '_reasoning') and result._reasoning:
        thinking_parts.append(result._reasoning)

    return "\n\n".join(thinking_parts) if thinking_parts else None


def extract_thinking_from_openrouter_response(response: dict) -> Optional[str]:
    """
    Extract reasoning from OpenRouter response.
    Claude models via OpenRouter may include reasoning in the response.
    """
    if not settings.USE_COT_REASONING:
        return None

    try:
        choices = response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            # Check for reasoning field (Claude extended thinking)
            if "reasoning" in message:
                return message["reasoning"]
            # Check for thinking field (some providers)
            if "thinking" in message:
                return message["thinking"]
    except Exception as e:
        logger.debug(f"Error extracting reasoning from OpenRouter response: {e}")

    return None


class LLMOutput(BaseModel):
    """Output schema for LLM node"""
    text: str
    model: str
    tokens_used: Optional[int] = None


class LLMExecutor(BaseNodeExecutor):
    """Executor for LLM node - calls language models via LiteLLM with timeout support"""

    node_type = "llm"

    # LLM calls external APIs (OpenAI, Anthropic, etc.)
    makes_external_calls = True

    # Timeout configuration
    LLM_TIMEOUT_SECONDS = 60.0

    def _get_workflow_state_from_context(self, context: NodeExecutionContext) -> Optional[WorkflowState]:
        """
        Extract workflow state from execution context (C2 fix).

        Args:
            context: Node execution context with variables

        Returns:
            WorkflowState if found in context, None otherwise
        """
        state_value = context.variables.get("sys.workflow_state")
        if state_value:
            try:
                return WorkflowState(state_value)
            except ValueError:
                logger.warning(f"Invalid workflow state: {state_value}")
        return None

    def _add_state_context_to_prompt(
        self,
        prompt: str,
        state: WorkflowState,
        session_id: Optional[str] = None
    ) -> str:
        """
        Add state machine context to the prompt for agent awareness (C2 fix).

        This helps the agent understand its current operating context and
        available capabilities based on workflow state.

        Args:
            prompt: Original user/system prompt
            state: Current workflow state
            session_id: Optional session ID for logging

        Returns:
            Enhanced prompt with state context
        """
        from app.agents.state_machine import get_valid_tools_for_state

        valid_tools = get_valid_tools_for_state(state)
        # Show first 10 tools to avoid overwhelming the prompt
        tools_preview = sorted(list(valid_tools))[:10]
        tools_str = ", ".join(tools_preview)
        if len(valid_tools) > 10:
            tools_str += f"... (+{len(valid_tools) - 10} more)"

        state_context = (
            f"\n[Workflow State: {state.value}]\n"
            f"[Available Tools: {tools_str}]\n"
        )

        return state_context + prompt
    
    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        start_time = time.time()

        try:
            node_data = context.node_data

            # Get model configuration
            model_id = normalize_model_id(node_data.get("model", "openai/gpt-4o-mini"))
            temperature = node_data.get("temperature", 0.7)
            max_tokens = node_data.get("maxTokens", 4096)

            logger.debug(f"LLM Executor - model_id: {model_id}")

            # Get prompts with variable substitution
            # The AI step builder may generate "prompt" instead of "systemPrompt"/"userPrompt",
            # so we check for all variants.
            system_prompt = node_data.get("systemPrompt", "You are a helpful assistant.")
            user_prompt = node_data.get("userPrompt", "") or node_data.get("prompt", "")

            system_prompt = self.substitute_variables(
                system_prompt, context.variables, context.inputs
            )
            user_prompt = self.substitute_variables(
                user_prompt, context.variables, context.inputs
            )

            # C2: Add state machine context to system prompt if enabled
            if settings.USE_STATE_MACHINE:
                workflow_state = self._get_workflow_state_from_context(context)
                if workflow_state:
                    session_id = context.variables.get("sys.session_id")
                    system_prompt = self._add_state_context_to_prompt(
                        system_prompt, workflow_state, session_id
                    )
                    logger.info(
                        f"LLM Executor - State machine enabled, "
                        f"state: {workflow_state.value}"
                    )

            # If no user prompt, use query from inputs or variables
            if not user_prompt:
                user_prompt = context.variables.get("sys.query", "")
                if not user_prompt:
                    for node_id, output in context.inputs.items():
                        if isinstance(output, dict) and "query" in output:
                            user_prompt = output["query"]
                            break
                        if isinstance(output, dict) and "text" in output:
                            user_prompt = output["text"]
                            break

            # Resolve API key via unified gateway (with OpenRouter fallback)
            tenant_uuid = UUID(context.tenant_id) if context.tenant_id else None
            api_key, model_id = await resolve_key_with_fallback(db, tenant_uuid, model_id)

            if not api_key:
                provider_type = get_provider_from_model(model_id)
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=f"API key not configured for provider '{provider_type}'. Add your key in Settings > LLM Providers.",
                    elapsed_ms=int((time.time() - start_time) * 1000)
                )

            # Call LLM via LiteLLM (unified path for all providers)
            try:
                response = await asyncio.wait_for(
                    litellm.acompletion(
                        model=model_id,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=temperature,
                        max_tokens=max_tokens,
                        api_key=api_key,
                    ),
                    timeout=self.LLM_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.warning(f"LLM call timed out after {self.LLM_TIMEOUT_SECONDS}s for node {context.node_id}")
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=f"LLM call timed out after {self.LLM_TIMEOUT_SECONDS} seconds",
                    elapsed_ms=elapsed_ms
                )

            elapsed_ms = int((time.time() - start_time) * 1000)

            # Extract response — reasoning models (kimi, DeepSeek R1, QwQ)
            # may return content in reasoning_content instead of content
            message = response.choices[0].message
            output_text = message.content or ""
            if not output_text:
                # Fallback: reasoning models via OpenRouter
                reasoning_content = getattr(message, "reasoning_content", None)
                if reasoning_content:
                    output_text = reasoning_content
                elif hasattr(message, "model_extra") and message.model_extra:
                    output_text = message.model_extra.get("reasoning_content", "") or message.model_extra.get("reasoning", "")
            tokens_used = getattr(response.usage, "total_tokens", None) if response.usage else None

            # Extract reasoning (CoT) from response
            reasoning = None
            if getattr(settings, 'USE_COT_REASONING', False):
                resp_dict = response.model_dump() if hasattr(response, "model_dump") else {}
                reasoning = extract_thinking_from_openrouter_response(resp_dict)

            return NodeExecutionResult(
                node_id=context.node_id,
                status="success",
                output={
                    "text": output_text,
                    "model": model_id,
                    "message": output_text,
                },
                elapsed_ms=elapsed_ms,
                tokens_used=tokens_used,
                reasoning=reasoning
            )

        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.error(f"LLM execution failed for node {context.node_id}: {e}")
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=str(e),
                elapsed_ms=elapsed_ms
            )

