"""
Node Executors for Dify-compatible Workflow Engine
Each executor handles a specific node type in the workflow graph

Phase 1 Security: Data Classification support added.
- NodeExecutionResult includes classification field
- BaseNodeExecutor includes makes_external_calls flag and export control hook
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import json
import logging
from pydantic import BaseModel, field_validator
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.classification import DataClassification

logger = logging.getLogger(__name__)


class NodeExecutionContext(BaseModel):
    """Context passed between nodes during execution"""
    node_id: str
    node_type: str
    node_data: Dict[str, Any]
    inputs: Dict[str, Any]  # Outputs from predecessor nodes
    variables: Dict[str, Any]  # Global workflow variables
    user_id: str
    orchestrator: Optional[Any] = None  # Reference to WorkflowOrchestrator
    workflow_spec: Optional[Dict[str, Any]] = None  # Full workflow specification

    # Additional context for skill execution
    tenant_id: Optional[str] = None  # Tenant ID for multi-tenancy
    session_id: Optional[str] = None  # Session ID for audit trails
    workflow_id: Optional[str] = None  # Workflow definition ID
    workflow_run_id: Optional[str] = None  # Workflow execution run ID
    db: Optional[Any] = None  # AsyncSession for database operations
    # Engine-run identity (plan 003 U21): the node's own engine task, which a parked node is signalled on,
    # and the run attempt it belongs to. Absent for the legacy in-process orchestrator.
    node_task_id: Optional[str] = None
    run_attempt: Optional[int] = None

    class Config:
        arbitrary_types_allowed = True


class NodeExecutionResult(BaseModel):
    """Result from executing a node"""
    node_id: str
    status: str  # 'success', 'failed', 'skipped', 'blocked'
    output: Optional[Any] = None
    error: Optional[str] = None
    elapsed_ms: int = 0
    tokens_used: Optional[int] = None
    reasoning: Optional[str] = None  # CoT reasoning from LLM
    next_node_id: Optional[str] = None  # For branching (IF/ELSE)
    timestamp: str = ""
    # Data classification for high-water mark propagation
    classification: DataClassification = DataClassification.INTERNAL

    @field_validator("status", mode="before")
    @classmethod
    def _canonical_failed(cls, value: Any) -> Any:
        """Single failure status: legacy "error"/"failure" mean "failed"."""
        if value in ("error", "errored", "failure", "fail"):
            return "failed"
        return value

    def __init__(self, **data):
        if not data.get("timestamp"):
            data["timestamp"] = datetime.utcnow().isoformat()
        super().__init__(**data)


class BaseNodeExecutor(ABC):
    """
    Abstract base class for all node executors
    Each node type (LLM, HTTP, Code, etc.) implements this interface

    Phase 1 Security: Export control support
    - Set makes_external_calls = True for executors that call external APIs
    - Use execute_with_export_control() wrapper for automatic enforcement
    """

    node_type: str = "base"

    # Flag to indicate if this executor makes external API calls
    # Executors that set this to True will have export control applied automatically
    makes_external_calls: bool = False

    async def execute_with_export_control(
        self,
        context: NodeExecutionContext,
        db: AsyncSession,
        input_classification: DataClassification,
    ) -> NodeExecutionResult:
        """
        Wrapper that enforces export control before execute().
        Called by WorkflowOrchestrator for automatic enforcement.

        Args:
            context: Execution context with node data and inputs
            db: Database session
            input_classification: Classification of input data (max of predecessors)

        Returns:
            NodeExecutionResult with classification field set
        """
        blocked = await self.export_block(context, input_classification)
        if blocked is not None:
            return blocked

        # Execute the node
        result = await self.execute(context, db)

        # Ensure classification is set (propagate input if not explicitly set)
        if result.classification == DataClassification.INTERNAL:
            # Preserve input classification if no explicit override
            result.classification = input_classification

        return result

    async def export_block(
        self,
        context: NodeExecutionContext,
        input_classification: DataClassification,
    ) -> Optional[NodeExecutionResult]:
        """A ``blocked`` result (and an audit entry) when this node may not receive the data, else None."""
        from app.config import get_settings

        if self.makes_external_calls and get_settings().USE_DATA_CLASSIFICATION:
            if not input_classification.can_export_to_external():
                await self._log_export_violation_async(context, input_classification)
                return NodeExecutionResult(
                    node_id=context.node_id,
                    status="blocked",
                    error=(
                        f"Export Control: {input_classification.display_name} data "
                        f"cannot be sent to external APIs. Node type '{self.node_type}' "
                        f"makes external calls and requires PUBLIC or INTERNAL data."
                    ),
                    classification=input_classification,
                )
        return None

    async def prepare(
        self,
        context: NodeExecutionContext,
        db: AsyncSession,
    ) -> Any:
        """Node gateway ``prepare`` hook (plan 003 U13, KTD4).

        Returns a ``NodeExecutionResult`` (wrapped as ``final`` by the gateway) or a U33 prepare body
        (``llm``/``wait``/``fanout`` dict). The default wraps ``execute``; export control and
        adornments are applied by the gateway around this call.
        """
        return await self.execute(context, db)

    async def finish(
        self,
        context: NodeExecutionContext,
        db: AsyncSession,
        finish_input: Any,
    ) -> NodeExecutionResult:
        """Node gateway ``finish`` hook: turn a generation answer / wake / fan-out summary into a result.

        Executors whose ``prepare`` can return ``llm`` or ``wait`` override this (U14, U15).
        """
        raise NotImplementedError(f"node type '{self.node_type}' has no finish step")

    async def _log_export_violation_async(
        self,
        context: NodeExecutionContext,
        classification: DataClassification,
    ) -> None:
        """
        Non-blocking audit log via Redis queue.
        Violations are written to a Redis list and processed by a background worker.
        """
        try:
            import redis.asyncio as redis
            from app.config import get_settings

            settings = get_settings()
            r = redis.from_url(settings.REDIS_URL)

            await r.rpush("audit:classification", json.dumps({
                "event_type": "EXPORT_BLOCKED",
                "user_id": str(context.user_id) if context.user_id else None,
                "node_id": context.node_id,
                "node_type": self.node_type,
                "classification": classification.value,
                "timestamp": datetime.utcnow().isoformat(),
            }))
            await r.close()
        except Exception as e:
            # Non-blocking: log error but don't fail the workflow
            logger.warning(f"Failed to log export violation to Redis: {e}")

    @abstractmethod
    async def execute(
        self,
        context: NodeExecutionContext,
        db: AsyncSession
    ) -> NodeExecutionResult:
        """
        Execute the node with given context
        
        Args:
            context: Execution context with node data and inputs
            db: Database session for API keys, etc.
            
        Returns:
            NodeExecutionResult with output or error
        """
        pass
    
    def substitute_variables(
        self,
        template: str,
        variables: Dict[str, Any],
        inputs: Dict[str, Any]
    ) -> str:
        """
        Substitute {{variable}} placeholders in template
        
        Supports:
        - {{node_id.output}} - Reference another node's output
        - {{variable_name}} - Reference a workflow variable
        - {{sys.query}} - System variables
        """
        result = template
        
        # Substitute input values (from predecessor nodes)
        for node_id, node_output in inputs.items():
            if isinstance(node_output, dict):
                # Handle nested outputs like {{node_id.text}}
                for key, value in node_output.items():
                    placeholder = f"{{{{{node_id}.{key}}}}}"
                    if placeholder in result:
                        result = result.replace(placeholder, str(value))
                
                # Also handle direct reference {{node_id}}
                placeholder = f"{{{{{node_id}}}}}"
                if placeholder in result:
                    # Use first string value or JSON
                    if 'output' in node_output:
                        result = result.replace(placeholder, str(node_output['output']))
                    elif 'text' in node_output:
                        result = result.replace(placeholder, str(node_output['text']))
                    else:
                        import json
                        result = result.replace(placeholder, json.dumps(node_output))
            else:
                placeholder = f"{{{{{node_id}}}}}"
                if placeholder in result:
                    result = result.replace(placeholder, str(node_output))
        
        # Substitute workflow variables
        for key, value in variables.items():
            placeholder = f"{{{{{key}}}}}"
            if placeholder in result:
                result = result.replace(placeholder, str(value))

            # Also handle {{sys.variable}} format for workflow variables
            # This allows templates to use {{sys.company}} which maps to variable "company"
            sys_placeholder = f"{{{{sys.{key}}}}}"
            if sys_placeholder in result:
                result = result.replace(sys_placeholder, str(value))

        return result


class PrepareResult:
    """Builders for the U33 ``prepare`` bodies and readers for the ``finish`` input (plan 003 U14).

    One-shot LLM nodes split into a pure ``build_prompt`` and a pure ``parse_answer``: ``prepare``
    returns ``PrepareResult.llm(...)`` and ``finish`` runs ``parse_answer`` over
    ``PrepareResult.answer_text(fin)``. The tier or model is a hint only; the engine policy picks the
    key, and no provider key ever leaves the backend.
    """

    @staticmethod
    def llm(
        *,
        system: str,
        prompt: str,
        replay_safe: bool = True,
        tier: Optional[str] = None,
        model: Optional[str] = None,
        tools: Optional[list] = None,
        max_rounds: Optional[int] = None,
        response_format: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "contractVersion": 1,
            "kind": "llm",
            "system": system,
            "prompt": prompt,
            "replaySafe": replay_safe,
        }
        if model:
            body["model"] = model
        elif tier:
            body["tier"] = tier
        if tools:
            body["tools"] = list(tools)
        if max_rounds:
            body["maxRounds"] = max_rounds
        if response_format:
            body["responseFormat"] = response_format
        return body

    @staticmethod
    def wait(
        *,
        duration_ms: Optional[int] = None,
        until_epoch_ms: Optional[int] = None,
        signal: Optional[str] = None,
        sla_until_epoch_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """A ``wait`` body: exactly one of a relative duration or an absolute wake time (KTD7)."""
        body: Dict[str, Any] = {"contractVersion": 1, "kind": "wait"}
        if duration_ms is not None:
            body["durationMs"] = int(duration_ms)
        if until_epoch_ms is not None:
            body["untilEpochMs"] = int(until_epoch_ms)
        if signal is not None:
            body["signal"] = signal
        if sla_until_epoch_ms is not None:
            body["slaUntilEpochMs"] = int(sla_until_epoch_ms)
        return body

    @staticmethod
    def answer_text(fin: Any) -> str:
        """The generation answer text from a ``finish`` input (a string, or ``{"text": ...}``)."""
        answer = getattr(fin, "answer", fin)
        if isinstance(answer, dict):
            for key in ("text", "answer", "content", "output"):
                if isinstance(answer.get(key), str):
                    return answer[key]
            return json.dumps(answer, default=str)
        if answer is None:
            return ""
        return answer if isinstance(answer, str) else json.dumps(answer, default=str)

    @staticmethod
    def answer_tokens(fin: Any) -> int:
        answer = getattr(fin, "answer", fin)
        if isinstance(answer, dict):
            for key in ("tokensUsed", "tokens_used", "totalTokens"):
                value = answer.get(key)
                if isinstance(value, int) and value >= 0:
                    return value
        return 0


class LlmKeyMissing(ValueError):
    """No API key for the model's provider (legacy in-process path only)."""


async def legacy_complete(
    db: AsyncSession,
    tenant_id: Optional[str],
    model: str,
    system: str,
    prompt: str,
    *,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """One in-process completion through ``llm_direct`` (the legacy ``execute`` wrappers, until U30).

    Returns ``{"text", "tokens", "model"}``. The engine path never calls this: it runs the same prompt
    as a child conversation under the engine policy and ledger.
    """
    import asyncio
    from uuid import UUID

    from app.services import llm_direct
    from app.services.llm_gateway import get_provider_from_model, normalize_model_id, resolve_key_with_fallback

    model_id = normalize_model_id(model)
    tenant_uuid = UUID(tenant_id) if tenant_id else None
    api_key, model_id = await resolve_key_with_fallback(db, tenant_uuid, model_id)
    provider_type = get_provider_from_model(model_id)
    if not api_key:
        raise LlmKeyMissing(
            f"API key not configured for provider '{provider_type}'. Add your key in Settings > LLM Providers."
        )
    response = await asyncio.wait_for(
        llm_direct.complete(
            llm_direct.DirectRequest(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                model=model_id,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
            llm_direct.DirectProvider(provider_type=provider_type, api_key=api_key),
        ),
        timeout=timeout,
    )
    return {
        "text": response.content or "",
        "tokens": (response.input_tokens or 0) + (response.output_tokens or 0),
        "model": model_id,
    }


# Registry of all node executors
_executor_registry: Dict[str, BaseNodeExecutor] = {}


def register_executor(executor: BaseNodeExecutor) -> None:
    """Register a node executor in the global registry"""
    _executor_registry[executor.node_type] = executor


def get_executor(node_type: str) -> Optional[BaseNodeExecutor]:
    """Get executor for a node type"""
    return _executor_registry.get(node_type)


def get_all_executors() -> Dict[str, BaseNodeExecutor]:
    """Get all registered executors"""
    return _executor_registry.copy()


# Import and register all executors
from app.node_executors.llm_executor import LLMExecutor
from app.node_executors.http_executor import HTTPExecutor
from app.node_executors.code_executor import CodeExecutor
from app.node_executors.condition_executor import ConditionExecutor
from app.node_executors.iteration_executor import IterationExecutor
from app.node_executors.classifier_executor import ClassifierExecutor
from app.node_executors.start_executor import StartExecutor
from app.node_executors.output_executor import OutputExecutor
from app.node_executors.template_executor import TemplateExecutor
from app.node_executors.aggregator_executor import AggregatorExecutor
from app.node_executors.knowledge_executor import KnowledgeExecutor
from app.node_executors.skill_executor import SkillExecutor
from app.node_executors.connector_executor import ConnectorExecutor
from app.node_executors.agent_executor import AgentExecutor
from app.node_executors.compliance_executor import ComplianceExecutor

# Phase 3: Essential workflow node executors
from app.node_executors.wait_executor import WaitExecutor
from app.node_executors.variable_assigner_executor import VariableAssignerExecutor
from app.node_executors.list_operator_executor import ListOperatorExecutor
from app.node_executors.document_extractor_executor import DocumentExtractorExecutor
from app.node_executors.parameter_extractor_executor import ParameterExtractorExecutor
from app.node_executors.ocr_executor import OCRExecutor
from app.node_executors.notification_executor import NotificationExecutor
from app.node_executors.webhook_trigger_executor import WebhookTriggerExecutor
from app.node_executors.subworkflow_executor import SubWorkflowExecutor
from app.node_executors.batch_executor import BatchExecutor
from app.node_executors.human_in_loop_executor import HumanInLoopExecutor

# Phase 3 Extension: Spreadsheet processing
from app.node_executors.spreadsheet_executor import SpreadsheetExecutor

# Phase 3.5: Vellum-inspired advanced features
from app.node_executors.guardrail_executor import GuardrailExecutor
from app.node_executors.merge_executor import MergeExecutor
from app.node_executors.error_executor import ErrorExecutor

# AI Workflow Builder: Complex step executors
from app.node_executors.complex_step_executor import MultiActionExecutor as MultiActionExecutor, ConditionalExecutor as ConditionalExecutor

# Register all executors
register_executor(StartExecutor())
register_executor(LLMExecutor())
register_executor(HTTPExecutor())
register_executor(CodeExecutor())
register_executor(ConditionExecutor())
register_executor(IterationExecutor())
register_executor(ClassifierExecutor())
register_executor(OutputExecutor())
register_executor(TemplateExecutor())
register_executor(AggregatorExecutor())
register_executor(KnowledgeExecutor())
register_executor(SkillExecutor())
register_executor(ConnectorExecutor())
register_executor(AgentExecutor())
register_executor(ComplianceExecutor())

# Phase 3: Essential workflow node executors
register_executor(WaitExecutor())
register_executor(VariableAssignerExecutor())
register_executor(ListOperatorExecutor())
register_executor(DocumentExtractorExecutor())
register_executor(ParameterExtractorExecutor())
register_executor(OCRExecutor())
register_executor(NotificationExecutor())
register_executor(WebhookTriggerExecutor())
register_executor(SubWorkflowExecutor())
register_executor(BatchExecutor())
register_executor(HumanInLoopExecutor())
register_executor(SpreadsheetExecutor())

# Phase 3.5: Vellum-inspired advanced features
register_executor(GuardrailExecutor())
register_executor(MergeExecutor())
register_executor(ErrorExecutor())

# AI Workflow Builder: Complex step executors
# Note: These are registered in complex_step_executor.py to avoid circular imports
