"""
Step Builder Agent - AI agent for conversational workflow building.

Uses GPT-4o via OpenRouter for natural language understanding and step generation.
Features:
- Understands workflow requests in natural language
- Proposes steps with proper structure
- Asks clarifying questions when needed
- Detects missing OAuth connectors
"""

from pydantic import BaseModel, Field, ValidationError
from pydantic_ai import Agent, RunContext
from typing import Optional, List, Literal
import logging
import json as json_module
import re

from app.agents.base import BaseAgent, AgentDependencies
from app.config import get_settings
from app.schemas.complex_steps import StepProposal, EdgeProposal

logger = logging.getLogger(__name__)


def serialize_workflow_dsl(steps: list[dict], edges: list[dict], max_field_len: int = 500) -> str:
    """Serialize full workflow graph for LLM context.

    Standalone function (not instance method) so it's testable in isolation
    and reusable by the security validation layer.
    """
    dsl: dict = {"nodes": [], "edges": []}
    for step in steps:
        node = {
            "id": step.get("id"),
            "type": step.get("type", "unknown"),
            "label": step.get("label", ""),
            "config": _strip_and_truncate(step.get("data", {}), max_field_len),
        }
        dsl["nodes"].append(node)
    for edge in edges:
        dsl["edges"].append({
            "source": edge.get("source"),
            "target": edge.get("target"),
            "sourceHandle": edge.get("sourceHandle"),
            "targetHandle": edge.get("targetHandle"),
        })
    return json_module.dumps(dsl, indent=2)


def _strip_and_truncate(config: dict, max_field_len: int = 500) -> dict:
    """Strip UI-only fields (prefixed with _) and truncate long values."""
    result = {}
    for k, v in config.items():
        if k.startswith("_"):  # convention: all _-prefixed keys are UI-only
            continue
        if k == "label":  # redundant with top-level label
            continue
        if isinstance(v, str) and len(v) > max_field_len:
            result[k] = v[:max_field_len] + f"... [truncated, {len(v)} chars total]"
        else:
            result[k] = v
    return result


# Response types — StepProposal and EdgeProposal imported from schemas.complex_steps


class QuestionOption(BaseModel):
    """An option for a clarifying question."""

    id: str
    label: str
    value: str


class ConnectorRequirement(BaseModel):
    """A connector that needs OAuth."""

    id: str
    name: str
    is_connected: bool = False


class StepBuilderResponse(BaseModel):
    """Response from the step builder agent."""

    message: str = Field(description="Response message to show to the user")
    message_type: Literal[
        "text", "steps_preview", "oauth_request", "question", "confirmation", "error"
    ] = Field(description="Type of message")
    proposed_steps: Optional[List[StepProposal]] = Field(
        None, description="Proposed changes to workflow"
    )
    proposed_edges: Optional[List[EdgeProposal]] = Field(
        None, description="Proposed edge connections between nodes"
    )
    required_connectors: Optional[List[ConnectorRequirement]] = Field(
        None, description="Connectors needing OAuth"
    )
    question_options: Optional[List[QuestionOption]] = Field(
        None, description="Options for clarifying questions"
    )


# System prompt for the step builder agent
STEP_BUILDER_SYSTEM_PROMPT = """You are a workflow builder AI. Your PRIMARY job is to CREATE and EDIT WORKFLOWS.

CRITICAL BEHAVIOR:
- ALWAYS respond with "steps_preview" message_type and proposed_steps
- Create a complete workflow on the FIRST response
- Only ask questions if you truly cannot proceed (rare)
- Never just chat - always propose actionable steps
- You can see the FULL workflow graph (nodes with configs, edges with handles)
- You can ADD, MODIFY, or DELETE nodes surgically

AVAILABLE NODE TYPES (use get_available_node_types tool for config details):
TRIGGERS: start, webhook_trigger, schedule_trigger
AI/LLM: llm, agent, skill
LOGIC: ifelse (branches: true/false), iteration (handles: loop/done), classifier (handles: per-class)
DATA: code, http, template, knowledge, connector, spreadsheet
TRANSFORM: aggregator, variable_assigner, list_operator, parameter_extractor
DOCUMENT: document_extractor, ocr
CONTROL: wait, human_in_loop, notification, subworkflow, notes
OUTPUT: output

SKILL NODES: Use the get_available_skills tool to discover available skills and check which have API keys configured. Use skillName (not skillId) in the config.

WORKFLOW CREATION RULES:
1. ALWAYS start with a trigger (start, webhook_trigger, or schedule_trigger)
2. ALWAYS end with an output step
3. Add ALL necessary steps in logical order
4. Use connector steps for external services (user will configure credentials later)
5. Configure steps with sensible defaults - leave placeholders where needed
6. Keep message brief - focus on the workflow, not explanation

EDITING RULES:
- To modify: set action="modify" and use the EXISTING node ID
- To delete: set action="delete" and use the EXISTING node ID
- To add: set action="add" with a new unique ID
- Reference existing nodes by their ID from the workflow DSL

EDGE PROPOSALS:
When proposing steps, also return proposed_edges to define connections.
The AI MUST propose explicit edges — do NOT rely on sequential ordering.
For branching nodes, use sourceHandle:
  - ifelse: sourceHandle="true" or sourceHandle="false"
  - classifier: sourceHandle="{class_name}"
  - iteration: sourceHandle="loop" (body) or sourceHandle="done" (exit)
For loop back-edges: target node uses targetHandle="loop-end"
When deleting a node, also propose new edges to reconnect the graph if needed.

TEMPLATE VARIABLES:
- {{sys.query}} - User-provided query/input from the start node
- {{node-id.result}} - Output from a previous node (e.g., {{code-1.result}}, {{http-1.text}})
- {{node-id.text}} - Text output from a previous node
- {{node-id.stdout}} - Console output from a code node

RESPONSE FORMAT:
- message_type: "steps_preview" (99% of responses)
- message: Brief 1-2 line summary of what the workflow does
- proposed_steps: Complete list of steps with action="add"/"modify"/"delete"
- proposed_edges: List of edge proposals with source, target, sourceHandle, targetHandle

Example - User: "Send Slack message when new email arrives"
{
  "message": "Here's your email-to-Slack workflow.",
  "message_type": "steps_preview",
  "proposed_steps": [
    {"id": "trigger-1", "type": "webhook_trigger", "label": "Email Received", "config": {"event": "email.received"}, "action": "add", "reason": "Trigger on new email"},
    {"id": "llm-1", "type": "llm", "label": "Extract Summary", "config": {"model": "gpt-4o-mini", "systemPrompt": "You are an email summarizer.", "userPrompt": "Summarize this email: {{trigger-1.text}}"}, "action": "add", "reason": "Summarize email content"},
    {"id": "slack-1", "type": "connector", "label": "Send to Slack", "config": {"connectorName": "slack", "operation": "postMessage", "channel": "#notifications"}, "action": "add", "reason": "Post to Slack channel"},
    {"id": "output-1", "type": "output", "label": "Done", "config": {}, "action": "add", "reason": "Workflow complete"}
  ],
  "proposed_edges": [
    {"source": "trigger-1", "target": "llm-1"},
    {"source": "llm-1", "target": "slack-1"},
    {"source": "slack-1", "target": "output-1"}
  ]
}

COMMON PATTERN — Research/Report Workflows:
For research, competitor analysis, or report-generation workflows, use skill nodes:
1. start → skill (web_search) → skill (browse_url) → llm (synthesize) → output
2. Call get_available_skills to confirm which skills exist and have API keys configured
3. Use skillName in config (e.g. "web_search", "browse_url", "extract_structured_data")
4. Chain multiple web_search skill nodes for different queries if needed
5. Use an llm node at the end to synthesize search results into a report

DO NOT:
- Ask "what would you like to do?" - just create the workflow
- Use oauth_request message_type - connectors are configured via step alerts
- Have long conversations - propose workflows immediately
- Wait for clarification on obvious requests
- Propose nodes of types not listed above
"""


class StepBuilderAgent(BaseAgent):
    """
    AI agent for conversational workflow building.

    Uses Claude 3.5 Sonnet via OpenRouter for:
    - Natural language understanding
    - Step proposal generation
    - Clarifying question generation
    - Connector detection
    """

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None):
        from app.services.llm_gateway import normalize_model_id, create_model

        settings = get_settings()

        # Normalize model id
        self.model = normalize_model_id(model or "openai/gpt-4o")

        self.name = "Step Builder"
        self.description = "AI assistant for building workflows through conversation"
        self.system_prompt = STEP_BUILDER_SYSTEM_PROMPT
        self.output_type = StepBuilderResponse

        # Resolve API key: prefer passed key, then env fallback
        resolved_key = api_key or settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY
        if not resolved_key:
            raise ValueError(
                "No API key provided. "
                "Please configure it in Settings > AI Models."
            )

        # Store for fallback text mode
        self._api_key = resolved_key

        # Create native pydantic-ai model
        pydantic_model = create_model(self.model, resolved_key)

        # Create Pydantic AI agent directly (bypass BaseAgent's model handling)
        self.agent = Agent(
            pydantic_model,
            output_type=StepBuilderResponse,
            system_prompt=STEP_BUILDER_SYSTEM_PROMPT,
            deps_type=AgentDependencies,
            retries=3,
        )

        # Register tools
        self._register_tools()

    def _register_tools(self):
        """Register tools available to the agent."""

        @self.agent.tool
        async def get_connected_connectors(
            ctx: RunContext[AgentDependencies],
        ) -> List[dict]:
            """Get list of connected OAuth connectors for the user."""
            # In a real implementation, this would query the database
            connectors = ctx.deps.connectors
            return [
                {
                    "id": cid,
                    "name": cfg.get("name", cid),
                    "is_connected": True,
                }
                for cid, cfg in connectors.items()
            ]

        @self.agent.tool
        async def get_available_skills(
            ctx: RunContext[AgentDependencies],
            category: Optional[str] = None,
        ) -> List[dict]:
            """Get available built-in skills. Returns name, description, category, required API keys, and whether those keys are configured.

            Use this to discover what skills exist (e.g. web_search, browse_url) before creating skill nodes.
            The api_configured flag tells you if the user has the required API keys set up.

            Args:
                category: Optional filter by category (e.g. 'web', 'code', 'financial')
            """
            from app.agents.skills.registry import SkillRegistry

            skills = SkillRegistry.list_skills()
            if category:
                skills = [s for s in skills if s.get("category", "").lower() == category.lower()]

            configured_providers = set(ctx.deps.api_keys.keys()) if ctx.deps.api_keys else set()

            for skill in skills:
                requires = skill.get("requires", [])
                if requires:
                    # For skills that accept multiple providers (e.g. web_search
                    # works with tavily OR exa OR serpapi), any one key suffices
                    skill["api_configured"] = any(
                        req in configured_providers for req in requires
                    )
                else:
                    skill["api_configured"] = True

            return skills

        @self.agent.tool
        async def get_available_node_types(
            ctx: RunContext[AgentDependencies],
            type_filter: Optional[str] = None,
        ) -> dict:
            """Get available node types with config schemas. Optionally filter by category or type name."""
            schemas = {
                "start": {
                    "description": "Workflow entry point with input variables",
                    "config": {"variables": [{"name": "string", "type": "string|number|boolean"}]},
                },
                "webhook_trigger": {
                    "description": "HTTP webhook trigger",
                    "config": {"event": "string", "method": "GET|POST"},
                },
                "schedule_trigger": {
                    "description": "Cron-based scheduled trigger",
                    "config": {"cron": "string", "timezone": "string"},
                },
                "llm": {
                    "description": "LLM/AI call",
                    "config": {"model": "string", "systemPrompt": "string", "userPrompt": "string", "temperature": "float", "maxTokens": "int"},
                    "note": "Use 'systemPrompt' and 'userPrompt', NOT 'prompt'.",
                },
                "agent": {
                    "description": "AI agent with tools and memory",
                    "config": {"agentType": "string", "model": "string", "tools": ["string"]},
                },
                "skill": {
                    "description": "Built-in skill execution (use get_available_skills tool to see available skills)",
                    "config": {"skillName": "string", "inputs": {}},
                },
                "ifelse": {
                    "description": "Conditional branch",
                    "config": {"conditions": [{"variable": "string", "operator": "string", "value": "string"}]},
                    "handles": {"source": ["true", "false"]},
                    "operators": ["equals", "not_equals", "contains", "not_contains", "greater_than", "less_than", "is_empty", "is_not_empty"],
                },
                "iteration": {
                    "description": "Loop over items or while condition",
                    "config": {"iterateOver": "string", "maxIterations": "int"},
                    "handles": {"source": ["loop", "done"]},
                },
                "classifier": {
                    "description": "Classify input into categories",
                    "config": {"classes": [{"name": "string", "description": "string"}], "model": "string"},
                    "handles": {"source": "dynamic — one handle per class name"},
                },
                "code": {
                    "description": "Python/JavaScript code execution",
                    "config": {"code": "string", "language": "python|javascript"},
                },
                "http": {
                    "description": "HTTP API request",
                    "config": {"url": "string", "method": "GET|POST|PUT|DELETE", "headers": {}, "body": "string"},
                },
                "template": {
                    "description": "Text templating with variable substitution",
                    "config": {"template": "string"},
                },
                "knowledge": {
                    "description": "RAG knowledge base search",
                    "config": {"knowledgeBaseId": "string", "query": "string", "topK": "int"},
                },
                "connector": {
                    "description": "External service integration (Slack, Gmail, Stripe, etc.)",
                    "config": {"connectorName": "string", "operation": "string"},
                },
                "spreadsheet": {
                    "description": "Spreadsheet data operations",
                    "config": {"operation": "read|write|append", "sheetId": "string"},
                },
                "aggregator": {
                    "description": "Aggregate multiple inputs into one output",
                    "config": {"strategy": "concatenate|merge|last"},
                },
                "variable_assigner": {
                    "description": "Assign values to workflow variables",
                    "config": {"assignments": [{"variable": "string", "value": "string"}]},
                },
                "list_operator": {
                    "description": "Operations on lists (filter, map, sort, etc.)",
                    "config": {"operation": "filter|map|sort|slice|flatten", "expression": "string"},
                },
                "parameter_extractor": {
                    "description": "Extract structured parameters from text using LLM",
                    "config": {"model": "string", "parameters": [{"name": "string", "type": "string", "description": "string"}]},
                },
                "document_extractor": {
                    "description": "Extract text from documents (PDF, DOCX, etc.)",
                    "config": {"format": "pdf|docx|txt"},
                },
                "ocr": {
                    "description": "Optical character recognition on images",
                    "config": {"language": "string"},
                },
                "wait": {
                    "description": "Delay/pause execution",
                    "config": {"duration": "int", "unit": "seconds|minutes|hours"},
                },
                "human_in_loop": {
                    "description": "Require human approval before continuing",
                    "config": {"message": "string", "approvers": ["string"]},
                },
                "notification": {
                    "description": "Send notification (email, Slack, SMS)",
                    "config": {"channel": "email|slack|sms", "recipient": "string", "message": "string"},
                },
                "subworkflow": {
                    "description": "Execute another workflow as a sub-step",
                    "config": {"workflowId": "string", "inputs": {}},
                },
                "notes": {
                    "description": "Documentation note (not executed)",
                    "config": {"content": "string"},
                },
                "output": {
                    "description": "Workflow output/end point",
                    "config": {"outputVariable": "string"},
                },
            }
            if type_filter:
                return {k: v for k, v in schemas.items() if type_filter.lower() in k}
            return schemas

    async def run(
        self,
        message: str,
        current_steps: List[dict],
        conversation_history: List[dict],
        deps: AgentDependencies,
        current_edges: Optional[List[dict]] = None,
    ) -> StepBuilderResponse:
        """
        Run the step builder agent.

        Args:
            message: User's message
            current_steps: Current workflow steps
            conversation_history: Recent conversation for context
            deps: Agent dependencies
            current_edges: Current workflow edges (source/target/handles)

        Returns:
            StepBuilderResponse with message and optional proposals
        """
        # Build context with full workflow DSL
        context = f"""Current workflow DSL:
{serialize_workflow_dsl(current_steps, current_edges or [])}

Recent conversation:
{self._format_history(conversation_history)}

User message: {message}"""

        try:
            result = await self.agent.run(context, deps=deps)
            return result.output
        except Exception as e:
            logger.warning(f"Structured output failed ({e}), trying text fallback")
            return await self._fallback_text_run(context)

    async def _fallback_text_run(self, context: str) -> StepBuilderResponse:
        """
        Fallback for models that don't support structured output/tool calling.

        Makes a direct chat completion call and parses JSON from the text response.
        """
        from openai import AsyncOpenAI

        settings = get_settings()

        try:
            # Determine provider and base_url from model ID
            provider = self.model.split("/")[0] if "/" in self.model else "openai"
            model_name = self.model.split("/", 1)[1] if "/" in self.model else self.model

            if provider == "openrouter":
                base_url = settings.OPENROUTER_BASE_URL
            elif provider == "anthropic":
                base_url = "https://api.anthropic.com/v1"
            else:
                base_url = None

            client = AsyncOpenAI(
                api_key=self._api_key,
                **({"base_url": base_url} if base_url else {}),
            )

            # Add JSON instruction to system prompt
            json_prompt = (
                self.system_prompt
                + "\n\nIMPORTANT: Respond with ONLY valid JSON matching this exact schema:\n"
                '{"message": "string", "message_type": "steps_preview", '
                '"proposed_steps": [{"id": "string", "type": "string", "label": "string", '
                '"config": {}, "action": "add", "position": 0, "reason": "string"}]}\n'
                "Do NOT include any text outside the JSON object. No markdown, no explanation."
            )

            response = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": json_prompt},
                    {"role": "user", "content": context},
                ],
                temperature=0.7,
            )

            text = response.choices[0].message.content or ""
            logger.debug(f"Fallback raw response: {text[:500]}")

            # Extract JSON from response (may be wrapped in markdown code block)
            json_text = text.strip()
            # Remove markdown code fences if present
            if json_text.startswith("```"):
                json_text = re.sub(r"^```(?:json)?\s*", "", json_text)
                json_text = re.sub(r"\s*```\s*$", "", json_text)

            # Find the outermost JSON object
            json_match = re.search(r"\{[\s\S]*\}", json_text)
            if json_match:
                data = json_module.loads(json_match.group())
                # Sanitize step proposals before Pydantic validation
                self._sanitize_steps(data)
                return StepBuilderResponse(**data)

            # No JSON found - return the text as a plain message
            logger.warning("No JSON found in fallback response, returning as text")
            return StepBuilderResponse(
                message=text,
                message_type="text",
            )

        except (json_module.JSONDecodeError, ValidationError) as e:
            logger.error(f"JSON/validation error in fallback: {e}")
            return StepBuilderResponse(
                message="I generated a workflow but couldn't format it properly. Please try again.",
                message_type="error",
            )
        except Exception as e:
            logger.error(f"Text fallback error: {e}")
            return StepBuilderResponse(
                message=f"Sorry, I encountered an error: {str(e)}",
                message_type="error",
            )

    @staticmethod
    def _sanitize_steps(data: dict) -> None:
        """Normalize LLM-generated step fields to match the Pydantic schema."""
        VALID_ACTIONS = {"add", "modify", "delete"}
        ACTION_MAP = {
            "update": "modify",
            "edit": "modify",
            "change": "modify",
            "create": "add",
            "new": "add",
            "remove": "delete",
        }
        VALID_MESSAGE_TYPES = {
            "text", "steps_preview", "oauth_request",
            "question", "confirmation", "error",
        }

        # Normalize message_type
        mt = data.get("message_type", "text")
        if mt not in VALID_MESSAGE_TYPES:
            data["message_type"] = "steps_preview" if data.get("proposed_steps") else "text"

        for step in data.get("proposed_steps") or []:
            action = step.get("action", "add")
            if action not in VALID_ACTIONS:
                step["action"] = ACTION_MAP.get(action, "add")
            # Ensure required fields have defaults
            if "reason" not in step:
                step["reason"] = step.get("label", "Workflow step")
            if "config" not in step:
                step["config"] = {}

    def _format_steps(self, steps: List[dict]) -> str:
        """Format current steps for context."""
        if not steps:
            return "No steps yet."

        lines = []
        for i, step in enumerate(steps, 1):
            step_type = step.get("type", "unknown")
            label = step.get("label", step.get("data", {}).get("label", step_type))
            lines.append(f"{i}. [{step_type}] {label}")
        return "\n".join(lines)

    def _format_history(self, history: List[dict]) -> str:
        """Format conversation history for context."""
        if not history:
            return "No previous messages."

        lines = []
        for msg in history[-5:]:  # Last 5 messages
            role = msg.get("role", "user")
            content = msg.get("content", "")[:200]  # Truncate long messages
            lines.append(f"{role}: {content}")
        return "\n".join(lines)


# Note: StepBuilderAgent is instantiated per-request with user's API key
# Do not create a singleton here
