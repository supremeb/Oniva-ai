"""Server-side agent configs for Deep Work on the engine (deep-work-on-engine plan, U6, KTD4/5/14).

One conversation, three phases (intake, plan, execute), each a different agent config applied with
``PUT /agent``. Prompts, tool lists and model tiers are built here; a client-supplied prompt or tool
list never reaches the engine. Keys are never in the config: the engine asks the policy endpoint
per request (purposes ``deep_work.*``, KTD14).

The pure builders take already-resolved inputs and are unit-tested without a database.
``resolve_deep_work_models`` does the model-tier lookups.
"""
import json
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from pydantic import ValidationError

from app.services.deep_work.models import DeepWorkPlan

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).parent / "deep_work" / "prompts"

# Subagent tool names; the engine worker registers the same list in ENGINE_SUBAGENT_TOOLS.
RESEARCH_WORKER = "research_worker"
REFLECTOR = "reflector"
OUTPUT_AGENT = "output_agent"
SUBAGENT_TOOLS = (RESEARCH_WORKER, REFLECTOR, OUTPUT_AGENT)

WEB_TOOLS = ("web_search", "browse_url")
KNOWLEDGE_TOOL = "search_knowledge"
FILE_TOOL = "read_file"
CONNECTOR_TOOLS = ("list_available_connectors", "get_connector_schema", "invoke_connector_tool")
SANDBOX_WORKER_TOOLS = ("shell_exec", "browser_navigate", "browser_screenshot")
SANDBOX_OUTPUT_TOOLS = ("shell_exec", "inspect_artifact", "publish_artifact")

ORCHESTRATOR_TOOLS = (
    "dw_start_round", "dw_get_learnings", "dw_write_section", "dw_finalize", "dw_get_report")
WORKER_SAVE_TOOL = "dw_save_learnings"
REFLECTOR_TOOLS = ("dw_get_learnings", "dw_record_reflection")

# Policy purposes (KTD14): the model policy maps each to a tier.
PURPOSE_ORCHESTRATOR = "deep_work.orchestrator"
PURPOSE_WORKER = "deep_work.worker"
PURPOSE_REFLECTION = "deep_work.reflection"
PURPOSE_SYNTHESIS = "deep_work.synthesis"
PURPOSE_OUTPUT = "deep_work.output"

# Legacy tiers: intake mid, plan/orchestrator/output high, reflection mid, workers by task type.
TIERS = ("high", "mid", "low")

MAX_PLAN_REPAIRS = 1
MAX_WORKER_SOURCES = 20


def load_prompt(filename: str) -> str:
    return (PROMPT_DIR / filename).read_text(encoding="utf-8")


def _with_date(prompt: str, now: datetime) -> str:
    date = now.strftime("%B %d, %Y")
    if "{{CURRENT_DATE}}" in prompt:
        return prompt.replace("{{CURRENT_DATE}}", date)
    return f"{prompt}\n\n[Context: Today's date is {date}.]"


@dataclass
class DeepWorkModels:
    """Resolved model ids per tier, plus the worker model chosen by task routing."""

    high: str
    mid: str
    low: str
    worker: Optional[str] = None

    def tier(self, name: str) -> str:
        return getattr(self, name)


@dataclass
class ExecuteInputs:
    """Everything the execute config depends on (already loaded, tenant-scoped)."""

    plan: DeepWorkPlan
    models: DeepWorkModels
    has_search: bool = False          # tenant has a search/browse capability configured
    knowledge_base_ids: List[str] = field(default_factory=list)  # accessible KBs only
    connector_ids: List[str] = field(default_factory=list)       # valid connectors only
    sandbox_enabled: bool = False
    run_id: Optional[str] = None
    max_rounds: Optional[int] = None
    deadline_ms: Optional[int] = None


# --- intake and plan -------------------------------------------------------------------------


def intake_agent_config(models: DeepWorkModels, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Clarify phase: no tools, the legacy intake prompt."""
    now = now or datetime.now(timezone.utc)
    return {
        "systemPrompt": _with_date(load_prompt("intake.md"), now),
        "tools": [],
        "model": models.mid,
        "metadata": {"purpose": PURPOSE_ORCHESTRATOR, "phase": "intake", "subagents": {}},
    }


def plan_agent_config(models: DeepWorkModels, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Plan phase: no tools, the legacy orchestrator (planner) prompt; the answer is ``DeepWorkPlan`` JSON."""
    now = now or datetime.now(timezone.utc)
    return {
        "systemPrompt": _with_date(load_prompt("orchestrator.md"), now),
        "tools": [],
        "model": models.high,
        "metadata": {"purpose": PURPOSE_ORCHESTRATOR, "phase": "plan", "subagents": {}},
    }


# --- plan parsing (one repair submission) -------------------------------------------------------


class PlanInvalid(Exception):
    """The plan answer is still invalid after the repair submission (surfaces as 422 with the text)."""

    def __init__(self, text: str, error: str):
        super().__init__(error)
        self.text = text
        self.error = error


def parse_plan(text: str) -> DeepWorkPlan:
    """Parse a plan answer: bare JSON or a fenced block. Raises ``ValueError`` / ``ValidationError``."""
    candidates = [text.strip()]
    candidates += re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    last: Exception = ValueError("no JSON object in the plan answer")
    for c in candidates:
        try:
            return DeepWorkPlan.model_validate_json(c)
        except (ValidationError, ValueError) as exc:
            last = exc
    raise last


def repair_message(error: str) -> str:
    return (
        "Your plan was not valid. Reply with ONLY the corrected JSON object for the plan schema "
        f"(no prose, no code fence needed). Problem: {error[:800]}")


async def plan_with_repair(
    first_text: str, repair: Callable[[str], Awaitable[str]],
) -> DeepWorkPlan:
    """Validate the plan answer; on failure submit exactly one repair, then raise ``PlanInvalid``.

    ``repair`` takes the repair message, submits it to the conversation and returns the answer text.
    """
    try:
        return parse_plan(first_text)
    except (ValidationError, ValueError) as exc:
        error = str(exc)
    second = await repair(repair_message(error))
    try:
        return parse_plan(second)
    except (ValidationError, ValueError) as exc:
        raise PlanInvalid(second, str(exc)) from exc


# --- model routing ---------------------------------------------------------------------------


def dominant_task(plan: DeepWorkPlan) -> Tuple[str, Optional[str]]:
    """The most common (task_type, output_type) among the plan's workers (ties: first seen)."""
    if not plan.sub_queries:
        return "research", None
    counts = Counter((q.task_type, q.output_type) for q in plan.sub_queries)
    best = max(counts.values())
    for q in plan.sub_queries:
        if counts[(q.task_type, q.output_type)] == best:
            return q.task_type, q.output_type
    return "research", None  # pragma: no cover


async def resolve_deep_work_models(
    db: Any, tenant_id: Any, plan: Optional[DeepWorkPlan] = None,
    agent_config: Optional[Dict[str, Any]] = None,
) -> DeepWorkModels:
    """Tier models with the legacy precedence (``deep_work_models`` override, routing policy, family tiers).

    Workers use ``resolve_model_for_policy`` for the plan's dominant task type, mid tier without a plan.
    """
    from app.services import llm_gateway as gw

    tiers = {t: await gw.resolve_model_for_tier(db, tenant_id, t, agent_config) for t in TIERS}
    worker = tiers["mid"]
    if plan is not None:
        task_type, output_type = dominant_task(plan)
        try:
            worker = await gw.resolve_model_for_policy(db, tenant_id, task_type, output_type, agent_config)
        except Exception:
            logger.warning("worker model routing failed; using mid tier", exc_info=True)
    return DeepWorkModels(high=tiers["high"], mid=tiers["mid"], low=tiers["low"], worker=worker)


# --- execute ---------------------------------------------------------------------------------


def default_max_rounds(reflection_rounds: int, max_sections: int = 15) -> int:
    """Orchestrator model-request cap: research rounds (start, workers, reflect), outline, sections, finish."""
    return (reflection_rounds + 1) * 4 + max_sections + 12


def default_deadline_ms(duration_sec: int, now_ms: Optional[int] = None) -> int:
    return (now_ms if now_ms is not None else int(time.time() * 1000)) + duration_sec * 1000


def _dedupe(items: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(i for i in items if i))


def worker_tools(inputs: ExecuteInputs) -> List[str]:
    tools: List[str] = [FILE_TOOL, WORKER_SAVE_TOOL]
    if inputs.has_search:
        tools += list(WEB_TOOLS)
    if inputs.knowledge_base_ids:
        tools.append(KNOWLEDGE_TOOL)
    if inputs.connector_ids:
        tools += list(CONNECTOR_TOOLS)
    if inputs.sandbox_enabled:
        tools += list(SANDBOX_WORKER_TOOLS)
    return sorted(_dedupe(tools))


def has_output_agent(inputs: ExecuteInputs) -> bool:
    return bool(inputs.plan.deliverable) and inputs.sandbox_enabled


def compose_execute_config(
    inputs: ExecuteInputs, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Execute-phase agent config: orchestrator loop prompt, ``dw_*`` tools, the subagent tools and
    ``metadata.subagents`` overrides (model, tools, prompt per child)."""
    now = now or datetime.now(timezone.utc)
    models = inputs.models
    with_output = has_output_agent(inputs)

    subagents: Dict[str, Dict[str, Any]] = {
        RESEARCH_WORKER: {
            "model": models.worker or models.mid,
            "tools": worker_tools(inputs),
            "systemPrompt": _with_date(load_prompt("worker_engine.md"), now),
        },
        REFLECTOR: {
            "model": models.mid,
            "tools": sorted(REFLECTOR_TOOLS),
            "systemPrompt": _with_date(load_prompt("reflection_engine.md"), now),
        },
    }
    parent_tools: List[str] = list(ORCHESTRATOR_TOOLS) + [RESEARCH_WORKER, REFLECTOR]
    if with_output:
        subagents[OUTPUT_AGENT] = {
            "model": models.high,
            "tools": sorted(_dedupe(list(SANDBOX_OUTPUT_TOOLS) + ["dw_get_report"])),
            "systemPrompt": _with_date(load_prompt("output_generator.md"), now),
        }
        parent_tools.append(OUTPUT_AGENT)

    prompt_parts = [_with_date(load_prompt("orchestrator_loop.md"), now)]
    prompt_parts.append("## Approved plan\n\n```json\n" + inputs.plan.model_dump_json(indent=2) + "\n```")
    if not with_output:
        prompt_parts.append("This plan has no deliverable file: skip step 7 and do not look for `output_agent`.")

    metadata: Dict[str, Any] = {
        "purpose": PURPOSE_ORCHESTRATOR,
        "phase": "execute",
        "allowedConnectorIds": list(inputs.connector_ids),
        "knowledgeBaseIds": list(inputs.knowledge_base_ids),
        "subagents": subagents,
    }
    if inputs.run_id:
        metadata["deepWorkRunId"] = inputs.run_id

    agent: Dict[str, Any] = {
        "systemPrompt": "\n\n".join(prompt_parts),
        "tools": sorted(_dedupe(parent_tools)),
        "model": models.high,
        "metadata": metadata,
    }
    if inputs.max_rounds is not None:
        agent["maxRounds"] = inputs.max_rounds
    if inputs.deadline_ms is not None:
        agent["deadlineMs"] = inputs.deadline_ms
    return agent


def execute_content(plan: DeepWorkPlan) -> str:
    """Content of the execute submission: the user-approved (possibly edited) plan."""
    return "Run this approved research plan.\n\n" + json.dumps(plan.model_dump(), indent=2)
