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
from app.services.engine_chat_agent import tier_rank, compaction_settings

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
SANDBOX_WORKER_TOOLS = ("shell_exec", "dw_browser_navigate", "dw_browser_screenshot")
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
    # KBs (subset of knowledge_base_ids) that hold indexed documents; None = unknown, treat all as searchable.
    searchable_kb_ids: Optional[List[str]] = None
    has_files: bool = False           # the run has project/workspace files workers can read_file
    run_id: Optional[str] = None
    max_rounds: Optional[int] = None
    deadline_ms: Optional[int] = None
    # Context tier (U16): off | offload | offload+clear | full. Off leaves every output as it was before U16.
    tiers: str = "off"
    context_windows: Optional[Dict[str, int]] = None  # model id -> window, for the models the run can use


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


NO_WEB_PLAN_NOTE = (
    "## Tool availability\n\n"
    "Web search and page browsing are NOT available on this run. Do not put `web_search` or `browse_url` "
    "in any sub_query's `tools`, and do not write sub-queries that depend on live web research. Workers "
    "answer from their own knowledge (and any knowledge bases or connectors) and must flag uncertainty."
)


def plan_agent_config(
    models: DeepWorkModels, now: Optional[datetime] = None, has_search: bool = True,
) -> Dict[str, Any]:
    """Plan phase: no tools, the legacy orchestrator (planner) prompt; the answer is ``DeepWorkPlan`` JSON."""
    now = now or datetime.now(timezone.utc)
    prompt = _with_date(load_prompt("orchestrator.md"), now)
    if not has_search:
        prompt = f"{prompt}\n\n{NO_WEB_PLAN_NOTE}"
    return {
        "systemPrompt": prompt,
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


def searchable_kbs(inputs: ExecuteInputs) -> List[str]:
    kbs = inputs.knowledge_base_ids if inputs.searchable_kb_ids is None else inputs.searchable_kb_ids
    return [k for k in kbs if k in inputs.knowledge_base_ids]


def worker_tools(inputs: ExecuteInputs) -> List[str]:
    # read_file is offered only when the run has files to read: workers are not pointed at any other file
    # and guess names ("findings.json") when it is there with nothing behind it.
    tools: List[str] = [WORKER_SAVE_TOOL]
    # With tiers on a worker also gets read_file: an offloaded tool result is a pointer it must be able to follow.
    if inputs.has_files or tier_rank(inputs.tiers):
        tools.append(FILE_TOOL)
    if inputs.has_search:
        tools += list(WEB_TOOLS)
    if searchable_kbs(inputs):
        tools.append(KNOWLEDGE_TOOL)
    if inputs.connector_ids:
        tools += list(CONNECTOR_TOOLS)
    if inputs.sandbox_enabled:
        tools += list(SANDBOX_WORKER_TOOLS)
    return sorted(_dedupe(tools))


def _gathering_tools(inputs: ExecuteInputs, tools: set) -> set:
    """The worker's tools that fetch information; read_file counts only when there are project files to read."""
    names = {"web_search", KNOWLEDGE_TOOL, "invoke_connector_tool"} | ({FILE_TOOL} if inputs.has_files else set())
    return tools & names


def worker_tools_section(inputs: ExecuteInputs) -> str:
    """The "Your tools" block of the worker prompt, derived from the worker's real tool set."""
    tools = set(worker_tools(inputs))
    lines: List[str] = []
    if "web_search" in tools:
        lines.append("- `web_search` and `browse_url`: find pages, then read the two or three most promising.")
    if KNOWLEDGE_TOOL in tools:
        lines.append("- `search_knowledge`: search the attached knowledge bases when the sub-query may be covered there.")
    if FILE_TOOL in tools and not inputs.has_files:
        lines.append("- `read_file`: read tool outputs that were saved to files under `tool_results/` "
                     "(use `offset` and `limit`). There are no project files to read.")
    elif FILE_TOOL in tools and tier_rank(inputs.tiers):
        lines.append("- `read_file`: read the project files you were pointed to, and tool outputs saved under "
                     "`tool_results/` (use `offset` and `limit`). Do not guess file names.")
    elif FILE_TOOL in tools:
        lines.append("- `read_file`: read the project files you were pointed to. Do not guess file names.")
    if "invoke_connector_tool" in tools:
        lines.append("- `list_available_connectors`, `get_connector_schema`, `invoke_connector_tool`: query connected data sources.")
    if "shell_exec" in tools:
        lines.append("- `shell_exec` (sandbox): computation and analysis; see Code execution below.")
    lines.append("- `dw_save_learnings`: save your findings, once.")
    out = ["## Your tools", "", "These are the only tools you have. Do not call anything else.", ""] + lines
    gathering = _gathering_tools(inputs, tools)
    if not gathering:
        out += [
            "", "You have NO web search, knowledge base or file access on this run. Do not look for them. "
            "Answer the sub-query from your own knowledge: state what you know that is relevant and specific, "
            "be explicit about uncertainty (lower the `confidence` of anything you are unsure of, and say so in "
            "the `fact`), and never invent URLs, quotes, statistics or sources. Save each finding with "
            "`source_type` `model_knowledge` and no `source_url`, in one `dw_save_learnings` call. "
            "Do this even if you have little to add: save what you know, then answer.",
        ]
    elif "web_search" not in tools:
        out += ["", "You have NO web search on this run: use the tools above, and answer the rest from your own "
                "knowledge as `model_knowledge` learnings (no `source_url`), flagging uncertainty."]
    return "\n".join(out)


def worker_prompt(inputs: ExecuteInputs, now: datetime) -> str:
    """worker_engine.md with its tool-dependent parts replaced by what this run's worker really has."""
    text = load_prompt("worker_engine.md")
    tools = set(worker_tools(inputs))
    if "{{TOOLS_SECTION}}" not in text:
        return _with_date(text, now)
    step1 = ("Start with `web_search` to find pages; read the two or three most promising with `browse_url`. "
             if "web_search" in tools else "")
    if not _gathering_tools(inputs, tools):
        method = ("Work from your own knowledge: list the specific, relevant points you can state with reasonable "
                  "confidence, and mark what you are unsure of.")
    else:
        method = (step1 + "Use the other tools listed under Your tools only where they fit the sub-query. "
                  "Never guess file names or retry a tool that returns nothing.")
    text = text.replace("{{TOOLS_SECTION}}", worker_tools_section(inputs)).replace("{{METHOD}}", method)
    return _with_date(text, now)


def has_output_agent(inputs: ExecuteInputs) -> bool:
    return bool(inputs.plan.deliverable) and inputs.sandbox_enabled


ORCHESTRATOR_CACHE_TTL = "1h"
CONTEXT_NOTE_FILE = "orchestrator_context_note.md"  # appended to the orchestrator prompt only with tiers on


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
            "systemPrompt": worker_prompt(inputs, now),
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
    tiered = bool(tier_rank(inputs.tiers))
    if tiered:
        prompt_parts.append(load_prompt(CONTEXT_NOTE_FILE).strip())
    if not inputs.has_search:
        prompt_parts.append(
            "## Tool availability\n\nWeb search is NOT available on this run. Workers answer from their own "
            "knowledge (and any knowledge bases or connectors) and save it as `model_knowledge` learnings; "
            "do not tell a worker to search the web or browse pages.")
    prompt_parts.append("## Approved plan\n\n```json\n" + inputs.plan.model_dump_json(indent=2) + "\n```")
    if not with_output:
        prompt_parts.append("This plan has no deliverable file: skip step 7 and do not look for `output_agent`.")

    compaction = compaction_settings(tiers=inputs.tiers, context_windows=inputs.context_windows)
    if tiered:
        # Same block for the children (they inherit the parent's today; named here so a child override works).
        for sub in subagents.values():
            sub["compaction"] = dict(compaction)
        # The orchestrator has no read_file, so it could not follow an offload pointer: keep every result of
        # its own tools (all bounded, backend-generated dw_* tools and the subagent calls) inline.
        compaction = dict(compaction)
        compaction["excludeTools"] = _dedupe(list(compaction.get("excludeTools", [])) + parent_tools)

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
        # Window comes from the model policy; thresholds scale with the default basis.
        "compaction": compaction,
        # Rounds wait on workers for more than the default 5 minute cache lifetime.
        "cacheTtl": ORCHESTRATOR_CACHE_TTL,
    }
    if inputs.max_rounds is not None:
        agent["maxRounds"] = inputs.max_rounds
    if inputs.deadline_ms is not None:
        agent["deadlineMs"] = inputs.deadline_ms
    return agent


def execute_content(plan: DeepWorkPlan) -> str:
    """Content of the execute submission: the user-approved (possibly edited) plan."""
    return "Run this approved research plan.\n\n" + json.dumps(plan.model_dump(), indent=2)
