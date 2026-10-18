"""
LLM Node Executor
Builds the node prompt and parses the answer (plan 003 U14): ``prepare`` returns an ``llm`` body, the
engine runs it as a tool-less child conversation under its policy and ledger, and ``finish`` wraps the
answer. Node-level token streaming and ``StatefulAgent`` wrapping are not supported on the engine path;
the UI shows the final text. ``execute`` is a thin legacy wrapper over the same pure functions (until U30).

C2 Enhancement: State machine integration for tool safety.
When USE_STATE_MACHINE is enabled, agents are wrapped with StatefulAgent
to enforce tool access based on current workflow state.
"""

from app.node_executors import (
    BaseNodeExecutor,
    LlmKeyMissing,
    NodeExecutionContext,
    NodeExecutionResult,
    PrepareResult,
    legacy_complete,
)
from sqlalchemy.ext.asyncio import AsyncSession
import time
import asyncio
import logging

from pydantic import BaseModel
from typing import Any, Dict, Optional

from app.config import get_settings
from app.agents.state_machine import WorkflowState
from app.services.llm_gateway import normalize_model_id

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
    """Executor for LLM node - builds the prompt, parses the answer"""

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
    
    def build_prompt(self, context: NodeExecutionContext) -> Dict[str, Any]:
        """Pure: the system and user prompts and the model settings for this node."""
        node_data = context.node_data
        model_id = normalize_model_id(node_data.get("model", "openai/gpt-4o-mini"))

        # The AI step builder may generate "prompt" instead of "systemPrompt"/"userPrompt",
        # so we check for all variants.
        system_prompt = node_data.get("systemPrompt", "You are a helpful assistant.")
        user_prompt = node_data.get("userPrompt", "") or node_data.get("prompt", "")

        system_prompt = self.substitute_variables(system_prompt, context.variables, context.inputs)
        user_prompt = self.substitute_variables(user_prompt, context.variables, context.inputs)

        # C2: Add state machine context to system prompt if enabled
        if settings.USE_STATE_MACHINE:
            workflow_state = self._get_workflow_state_from_context(context)
            if workflow_state:
                session_id = context.variables.get("sys.session_id")
                system_prompt = self._add_state_context_to_prompt(system_prompt, workflow_state, session_id)

        # If no user prompt, use query from inputs or variables
        if not user_prompt:
            user_prompt = context.variables.get("sys.query", "")
            if not user_prompt:
                for output in context.inputs.values():
                    if isinstance(output, dict) and "query" in output:
                        user_prompt = output["query"]
                        break
                    if isinstance(output, dict) and "text" in output:
                        user_prompt = output["text"]
                        break

        return {
            "system": system_prompt,
            "prompt": user_prompt,
            "model": model_id,
            "temperature": node_data.get("temperature", 0.7),
            "max_tokens": node_data.get("maxTokens", 4096),
        }

    def parse_answer(self, context: NodeExecutionContext, text: str, tokens: int = 0) -> NodeExecutionResult:
        """Pure: wrap the generation text as the node result."""
        model_id = normalize_model_id(context.node_data.get("model", "openai/gpt-4o-mini"))
        output_text = text or ""
        return NodeExecutionResult(
            node_id=context.node_id,
            status="success",
            output={"text": output_text, "model": model_id, "message": output_text},
            tokens_used=tokens or None,
        )

    async def prepare(self, context: NodeExecutionContext, db: AsyncSession) -> Any:
        built = self.build_prompt(context)
        return PrepareResult.llm(
            system=built["system"],
            prompt=built["prompt"],
            model=built["model"] if context.node_data.get("model") else None,
            tier="low",
        )

    async def finish(self, context: NodeExecutionContext, db: AsyncSession, finish_input: Any) -> NodeExecutionResult:
        return self.parse_answer(
            context, PrepareResult.answer_text(finish_input), PrepareResult.answer_tokens(finish_input)
        )

    async def execute(self, context: NodeExecutionContext, db: AsyncSession) -> NodeExecutionResult:
        """Legacy in-process path over the same pure functions (removed by U30)."""
        start_time = time.time()
        try:
            built = self.build_prompt(context)
            try:
                completion = await legacy_complete(
                    db,
                    context.tenant_id,
                    built["model"],
                    built["system"],
                    built["prompt"],
                    temperature=built["temperature"],
                    max_tokens=built["max_tokens"],
                    timeout=self.LLM_TIMEOUT_SECONDS,
                )
            except LlmKeyMissing as exc:
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=str(exc),
                    elapsed_ms=int((time.time() - start_time) * 1000),
                )
            except asyncio.TimeoutError:
                logger.warning(f"LLM call timed out after {self.LLM_TIMEOUT_SECONDS}s for node {context.node_id}")
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="failed",
                    error=f"LLM call timed out after {self.LLM_TIMEOUT_SECONDS} seconds",
                    elapsed_ms=int((time.time() - start_time) * 1000),
                )
            result = self.parse_answer(context, completion["text"], completion["tokens"])
            result.output["model"] = completion["model"]
            if getattr(settings, "USE_COT_REASONING", False):
                result.reasoning = completion.get("reasoning")
            result.elapsed_ms = int((time.time() - start_time) * 1000)
            return result
        except Exception as e:
            logger.error(f"LLM execution failed for node {context.node_id}: {e}")
            return NodeExecutionResult(
                node_id=context.node_id,
                status="failed",
                error=str(e),
                elapsed_ms=int((time.time() - start_time) * 1000),
            )
