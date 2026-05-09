"""
Agent Generator Service (Phase A: Chat-First Creation).

Builds a meta-prompt from available skills/connectors/models,
calls an LLM to generate a complete agent configuration,
and streams the result as typed SSE events.
"""
import inspect
import json
import logging
from typing import AsyncGenerator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.schemas.agent_generation import (
    GeneratedAgentConfig,
    ClarifyQuestion,
    ClarifyResponse,
)
from app.services.llm_gateway import normalize_model_id

logger = logging.getLogger(__name__)

# Models the meta-agent can recommend (use provider/model format for frontend compatibility)
SUPPORTED_MODELS = [
    {"id": "openai/gpt-4o", "provider": "openai", "description": "Fast and capable general-purpose model"},
    {"id": "openai/gpt-4o-mini", "provider": "openai", "description": "Cost-efficient for simple tasks"},
    {"id": "anthropic/claude-3-5-sonnet-20241022", "provider": "anthropic", "description": "Strong reasoning and structured output"},
    {"id": "anthropic/claude-3-5-haiku-20241022", "provider": "anthropic", "description": "Fast and affordable"},
]

META_SYSTEM_PROMPT = """\
You are an expert AI agent architect for the Oniva platform.
Given a user's natural language description of what they want an agent to do,
you generate a complete agent configuration.

Your output MUST be valid JSON matching the schema below. Do NOT include markdown fences.

## Output Schema

{{
  "name": "string (short, descriptive agent name)",
  "description": "string (one-sentence summary)",
  "system_prompt": "string (detailed instructions the agent will follow; use numbered steps, be specific about tone and format)",
  "model": "string (one of the supported model IDs)",
  "temperature": number (0-2, lower for factual tasks, higher for creative),
  "max_tokens": number (100-128000, match expected output length),
  "skills": ["skill_name_1", "skill_name_2"],
  "connectors": ["connector_id_1"],
  "icon": "string (Phosphor icon name like Robot, MagnifyingGlass, ChartLineUp, ShieldCheck, BookOpenText, Database, ChatCircle, FileText, Lightning, Gear)",
  "agent_type": "custom",
  "reasoning": {{
    "skill_reasons": {{"skill_name": "why this skill is needed"}},
    "connector_reasons": {{"connector_id": "why this connector is needed"}},
    "model_reason": "why this model was chosen"
  }}
}}

## Available Skills

{skills_section}

## Available Connectors

{connectors_section}

## Supported Models

{models_section}

## Guidelines

1. Only select skills and connectors from the lists above. Never invent IDs.
2. Write detailed, actionable system prompts with numbered steps.
3. Choose the cheapest model that can handle the task. Use gpt-4o-mini for simple tasks, gpt-4o or claude-3-5-sonnet for complex analysis.
4. Set temperature low (0.1-0.3) for factual/compliance tasks, medium (0.5-0.7) for general tasks, high (0.8-1.0) for creative tasks.
5. Set max_tokens proportional to expected output: 2000 for short answers, 4000-8000 for reports.
6. Pick an icon that visually represents the agent's purpose.
"""


CLARIFY_SYSTEM_PROMPT = """\
You are helping a user create an AI agent. Given their description, generate 2-3 clarifying \
questions that would help produce a better agent configuration.

Return valid JSON (no markdown fences):
{{
  "questions": [
    {{
      "id": "q1",
      "text": "What is the primary audience for this agent?",
      "type": "multiple_choice",
      "options": ["Internal team", "External customers", "Both"]
    }},
    {{
      "id": "q2",
      "text": "What tone should the agent use?",
      "type": "multiple_choice",
      "options": ["Professional", "Casual", "Technical"]
    }}
  ],
  "should_generate": false
}}

Rules:
1. Ask 2-3 questions maximum.
2. Prefer multiple_choice with 2-4 concise options.
3. Use free_text only when options can't cover the answer space.
4. Focus on: audience, tone, scope, output format, data sources.
5. If the description is already extremely detailed (>200 words with clear constraints), \
set should_generate=true and return an empty questions list.
"""

STATIC_FALLBACK_QUESTIONS: list[dict] = [
    {
        "id": "audience",
        "text": "Who will primarily interact with this agent?",
        "type": "multiple_choice",
        "options": ["Internal team members", "External customers", "Both internal and external"],
    },
    {
        "id": "tone",
        "text": "What communication style should the agent use?",
        "type": "multiple_choice",
        "options": ["Professional & formal", "Friendly & conversational", "Technical & precise"],
    },
    {
        "id": "output",
        "text": "What kind of output should the agent produce?",
        "type": "multiple_choice",
        "options": ["Short answers & summaries", "Detailed reports & analysis", "Structured data & tables"],
    },
]


class AgentGeneratorService:
    """Generates agent configurations from natural language descriptions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get_available_skills(self, tenant_id: UUID) -> list[dict]:
        """Get skills available for this tenant."""
        from app.agents.skills.registry import SkillRegistry
        registry_skills = SkillRegistry.get_all_skills()
        skills = []
        for name, manifest in registry_skills.items():
            skills.append({
                "name": manifest.name,
                "description": manifest.description,
                "category": manifest.category,
            })
        return skills

    async def _get_available_connectors(self, tenant_id: UUID) -> list[dict]:
        """Get connectors available for this tenant."""
        from app.connectors.registry import ConnectorRegistry
        connectors = []
        for meta in ConnectorRegistry.list_available():
            connectors.append({
                "id": meta.id,
                "name": meta.name,
                "description": meta.description,
            })
        return connectors

    async def generate_clarifying_questions(
        self,
        description: str,
        context: list[dict[str, str]] | None = None,
        api_key: str | None = None,
        model: str | None = None,
        provider_type: str | None = None,
        api_base: str | None = None,
    ) -> ClarifyResponse:
        """Generate clarifying questions for a description, with static fallback."""
        try:
            raw_text = await self._call_llm(
                system_prompt=CLARIFY_SYSTEM_PROMPT,
                user_message=f"User wants to create an agent:\n\n{description}",
                api_key=api_key,
                model=model,
                provider_type=provider_type,
                api_base=api_base,
            )
            parsed = json.loads(raw_text)

            if parsed.get("should_generate", False):
                return ClarifyResponse(questions=[], should_generate=True)

            questions = [ClarifyQuestion(**q) for q in parsed.get("questions", [])]
            if not questions:
                raise ValueError("No questions generated")

            return ClarifyResponse(questions=questions, should_generate=False)

        except Exception as e:
            logger.warning(f"LLM clarify failed, using static fallback: {e}")
            return ClarifyResponse(
                questions=[ClarifyQuestion(**q) for q in STATIC_FALLBACK_QUESTIONS],
                should_generate=False,
            )

    def _build_meta_prompt(
        self,
        skills: list[dict],
        connectors: list[dict],
    ) -> str:
        """Build the system prompt for the meta-agent."""
        # Skills section
        if skills:
            from app.agents.skills.registry import SkillRegistry
            skills_lines = []
            for s in skills:
                sig = ""
                try:
                    skill_obj = SkillRegistry.get_skill(s['name'])
                    if skill_obj and skill_obj.handler:
                        params = inspect.signature(skill_obj.handler)
                        param_strs = []
                        for pname, param in params.parameters.items():
                            if pname == 'ctx':
                                continue
                            if param.default is not inspect.Parameter.empty:
                                param_strs.append(f"{pname}={param.default!r}")
                            else:
                                param_strs.append(pname)
                        if param_strs:
                            sig = f" ({', '.join(param_strs)})"
                except (ValueError, TypeError):
                    pass
                skills_lines.append(
                    f"- **{s['name']}** ({s.get('category', 'general')}): "
                    f"{s['description']}{sig}"
                )
            skills_section = "\n".join(skills_lines)
        else:
            skills_section = "No skills available."

        # Connectors section
        if connectors:
            conn_lines = []
            for c in connectors:
                conn_lines.append(f"- **{c['id']}** ({c['name']}): {c.get('description', 'No description')}")
            connectors_section = "\n".join(conn_lines)
        else:
            connectors_section = "No connectors available."

        # Models section
        models_lines = []
        for m in SUPPORTED_MODELS:
            models_lines.append(f"- **{m['id']}** ({m['provider']}): {m['description']}")
        models_section = "\n".join(models_lines)

        return META_SYSTEM_PROMPT.format(
            skills_section=skills_section,
            connectors_section=connectors_section,
            models_section=models_section,
        )

    def _validate_generated_config(
        self,
        config: GeneratedAgentConfig,
        valid_skill_names: set[str],
        valid_connector_ids: set[str],
        valid_model_ids: set[str],
    ) -> GeneratedAgentConfig:
        """Validate and sanitize generated config against known registries."""
        # Filter out hallucinated skills
        config.skills = [s for s in config.skills if s in valid_skill_names]

        # Filter out hallucinated connectors
        config.connectors = [c for c in config.connectors if c in valid_connector_ids]

        # Normalize model ID to provider/model format (e.g. "gpt-4o" -> "openai/gpt-4o")
        config.model = normalize_model_id(config.model)

        # Fallback model if hallucinated
        if config.model not in valid_model_ids:
            config.model = "openai/gpt-4o"
            config.reasoning.model_reason += " (original model not supported, defaulted to openai/gpt-4o)"

        return config

    async def generate_agent_stream(
        self,
        description: str,
        tenant_id: UUID,
        api_key: str | None = None,
        model: str | None = None,
        provider_type: str | None = None,
        api_base: str | None = None,
        clarification_context: list[dict[str, str]] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Generate an agent configuration and yield typed SSE event dicts.

        Event types:
          agent_name, agent_description, instruction_chunk, instruction_complete,
          skills_suggested, connectors_suggested, model_recommended,
          generation_complete, generation_error
        """
        try:
            # Gather context
            skills = await self._get_available_skills(tenant_id)
            connectors = await self._get_available_connectors(tenant_id)

            valid_skill_names = {s["name"] for s in skills}
            valid_connector_ids = {c["id"] for c in connectors}
            valid_model_ids = {m["id"] for m in SUPPORTED_MODELS}

            system_prompt = self._build_meta_prompt(skills, connectors)

            # Build user message with optional clarification context
            user_message = f"Create an agent for the following purpose:\n\n{description}"
            if clarification_context:
                clarifications = "\n".join(
                    f"Q: {item.get('question', '')}\nA: {item.get('answer', '')}"
                    for item in clarification_context
                )
                user_message += f"\n\n## Additional Context from Clarifying Questions\n\n{clarifications}"

            # Call LLM (use pre-fetched provider config from endpoint)
            raw_text = await self._call_llm(
                system_prompt=system_prompt,
                user_message=user_message,
                api_key=api_key,
                model=model,
                provider_type=provider_type,
                api_base=api_base,
            )

            # Parse JSON response
            try:
                parsed = json.loads(raw_text)
                config = GeneratedAgentConfig(**parsed)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Failed to parse LLM output: {e}")
                yield self._event("generation_error", {"message": f"Failed to parse AI response: {e}"})
                return

            # Validate against registries
            config = self._validate_generated_config(
                config, valid_skill_names, valid_connector_ids, valid_model_ids
            )

            # Stream events in sequence
            yield self._event("agent_name", {"name": config.name})
            yield self._event("agent_description", {"description": config.description})

            # Stream instructions in chunks for typing effect
            chunk_size = 80
            for i in range(0, len(config.system_prompt), chunk_size):
                chunk = config.system_prompt[i : i + chunk_size]
                yield self._event("instruction_chunk", {"delta": chunk})

            yield self._event("instruction_complete", {})

            # Skills
            skills_data = []
            for skill_name in config.skills:
                reason = config.reasoning.skill_reasons.get(skill_name, "")
                skills_data.append({"id": skill_name, "reason": reason})
            yield self._event("skills_suggested", {"skills": skills_data})

            # Connectors
            connectors_data = []
            for conn_id in config.connectors:
                reason = config.reasoning.connector_reasons.get(conn_id, "")
                connectors_data.append({"id": conn_id, "reason": reason})
            yield self._event("connectors_suggested", {"connectors": connectors_data})

            # Model
            yield self._event("model_recommended", {
                "model": config.model,
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
                "reason": config.reasoning.model_reason,
            })

            # Full config
            yield self._event("generation_complete", {"config": config.model_dump()})

        except Exception as e:
            logger.exception("Agent generation failed")
            yield self._event("generation_error", {"message": str(e)})

    async def _call_llm(
        self,
        system_prompt: str,
        user_message: str,
        api_key: str | None = None,
        model: str | None = None,
        provider_type: str | None = None,
        api_base: str | None = None,
    ) -> str:
        """Call the LLM and return the raw text response.

        Uses resolved tenant provider config passed from the API layer.
        Falls back to env vars only for bootstrap (before providers are configured).
        """
        # Resolve fallback values if not provided (bootstrap scenario)
        if not api_key or not provider_type:
            settings = get_settings()
            api_key = api_key or settings.OPENROUTER_API_KEY
            model = model or settings.DEFAULT_LLM_MODEL or "openai/gpt-4o"
            provider_type = provider_type or settings.DEFAULT_LLM_PROVIDER or "openrouter"
            api_base = api_base or settings.OPENROUTER_BASE_URL

        effective_model = model or "openai/gpt-4o"

        if provider_type in ("openrouter", "openai"):
            from openai import AsyncOpenAI

            client_kwargs = {"api_key": api_key}
            if provider_type == "openrouter":
                client_kwargs["base_url"] = api_base or "https://openrouter.ai/api/v1"
            elif api_base:
                client_kwargs["base_url"] = api_base

            client = AsyncOpenAI(**client_kwargs)
            response = await client.chat.completions.create(
                model=effective_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.3,
                max_tokens=4000,
            )
            return response.choices[0].message.content or ""

        elif provider_type == "anthropic":
            import anthropic

            # Strip "anthropic/" prefix if present (raw model from provider config)
            anthro_model = effective_model
            if anthro_model.startswith("anthropic/"):
                anthro_model = anthro_model[len("anthropic/"):]

            client = anthropic.AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model=anthro_model,
                max_tokens=4000,
                messages=[
                    {"role": "user", "content": user_message},
                ],
                system=system_prompt,
                temperature=0.3,
            )
            return response.content[0].text if response.content else ""

        else:
            # Unknown provider — try OpenAI-compatible as best effort
            from openai import AsyncOpenAI

            client_kwargs = {"api_key": api_key}
            if api_base:
                client_kwargs["base_url"] = api_base
            client = AsyncOpenAI(**client_kwargs)
            response = await client.chat.completions.create(
                model=effective_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.3,
                max_tokens=4000,
            )
            return response.choices[0].message.content or ""

    @staticmethod
    def _event(event_type: str, data: dict) -> dict:
        """Format as SSE event dict."""
        return {
            "event": event_type,
            "data": json.dumps(data),
        }
