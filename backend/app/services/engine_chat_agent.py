"""Server-side agent config for engine chat (chat-on-engine plan, U3 / KTD2, KTD3).

The browser sends only *selections*; this module turns them, the tenant's chat agent, the
project and the gateway manifest into the engine agent config (``systemPrompt``, ``tools``,
``metadata``). A client-supplied prompt or tool list never reaches the engine.

The pure part (``compose_agent_config``) takes already-loaded inputs and is unit-tested without a
database. ``load_chat_inputs`` does the tenant-scoped lookups and the RBAC drops.
"""
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.agent_resolver import (
    build_project_context,
    build_web_search_instruction,
    resolve_agent_settings,
)

logger = logging.getLogger(__name__)

WEB_TOOLS = ("web_search", "browse_url")
CONNECTOR_TOOLS = ("list_available_connectors", "get_connector_schema", "invoke_connector_tool")
KNOWLEDGE_TOOL = "search_knowledge"
FILE_TOOLS = ("read_file", "write_file")
CODE_TOOLS = ("execute_python",)
_SKILL_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "system.skills.oniva")

KNOWLEDGE_INSTRUCTION = (
    "Knowledge bases are selected for this conversation. Call search_knowledge first, before "
    "answering any question their documents might cover, and cite what it returns."
)

# Capability-honest prompt (U4 / R4, KTD4): the block names the tool families actually granted, so
# the model never offers a capability it lacks. Families are ordered as the product names them.
CAPABILITY_HEADING = "Your capabilities in this conversation"
CAPABILITY_FOOTER = (
    "Any capability not listed above is unavailable in this conversation. Do not offer it, "
    "promise it, or claim you can do it."
)
CAPABILITY_FAMILIES = (
    ("web search", WEB_TOOLS),
    ("knowledge base search", (KNOWLEDGE_TOOL,)),
    ("connectors", CONNECTOR_TOOLS),
    ("file operations", FILE_TOOLS),
    ("code execution", CODE_TOOLS),
)

# Tiered context (U16). With tiers on, chat also gets read_file (to page saved tool outputs) and, top level only,
# update_todo (a rewritten todo is what survives a summary). The note lists where saved outputs live.
TODO_TOOL = "update_todo"
OFFLOAD_NOTE = (
    "Large tool outputs may be saved to files under tool_results/ instead of appearing in full; "
    "read them with read_file using offset and limit."
)


# Compaction (U11, R9). The agent config names no model (the policy endpoint picks it per
# request and returns the chosen model's ``contextWindow``, which the engine uses for the
# thresholds). DEFAULT_CONTEXT_WINDOW only sizes the reserve/margin/keep-recent token counts
# and is the engine's fallback when the policy gave no window. Background compaction starts at COMPACT_FRACTION of the window; a request waits for one
# once only the reply reserve is left.
ASK_USER_TOOL = "ask_user"
DEFAULT_CONTEXT_WINDOW = 200_000
COMPACT_FRACTION = 0.70
RESERVE_FRACTION = 0.10
KEEP_RECENT_FRACTION = 0.10


# Tools whose results are never cleared from the view (U16): their content is the state the model works from.
CLEAR_EXCLUDE_TOOLS = (
    "dw_get_learnings", "dw_get_report", "ask_user", "update_todo", "present_plan", "complete_task")


def tier_rank(tiers: str) -> int:
    from app.services.feature_flags import CONTEXT_TIER_VALUES

    return CONTEXT_TIER_VALUES.index(tiers) if tiers in CONTEXT_TIER_VALUES else 0


def compaction_settings(
    context_window: Optional[int] = None, compact_fraction: float = COMPACT_FRACTION,
    *, tiers: str = "off", context_windows: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Engine ``CompactionOverrides`` for a conversation (camelCase, engine schema).

    ``context_window`` None (the default) leaves the window to the model policy, which returns the
    chosen model's; pass a number to pin it. ``tiers`` (``off`` | ``offload`` | ``offload+clear`` | ``full``)
    adds the context-reduction knobs: nothing at ``off`` (the block is then exactly the pre-U16 one);
    ``offloadTokens`` from ``offload``; clearing from ``offload+clear``; ``structuredSummary`` and
    ``archiveSummarized`` at ``full``. ``context_windows`` (model id -> tokens) is emitted when given and,
    with clearing on, the smallest known window sizes the clear trigger.
    """
    basis = context_window or DEFAULT_CONTEXT_WINDOW
    reserve = int(basis * RESERVE_FRACTION)
    background_start = int(basis * compact_fraction)
    out: Dict[str, Any] = {
        "enabled": True,
        **({"contextWindow": context_window} if context_window else {}),
        "reserveTokens": reserve,
        # Engine: background starts at window - reserve - margin == background_start.
        "backgroundMargin": max(0, basis - reserve - background_start),
        "keepRecentTokens": int(basis * KEEP_RECENT_FRACTION),
    }
    rank = tier_rank(tiers)
    if rank == 0:
        return out
    cfg = get_settings()
    windows = {k: int(v) for k, v in (context_windows or {}).items() if v}
    if windows:
        out["contextWindows"] = windows
    out["offloadTokens"] = cfg.ENGINE_CONTEXT_OFFLOAD_TOKENS
    if rank >= 2:
        clear_basis = context_window or (min(windows.values()) if windows else DEFAULT_CONTEXT_WINDOW)
        out["clearTriggerTokens"] = int(clear_basis * cfg.ENGINE_CONTEXT_CLEAR_FRACTION)
        out["keepToolResults"] = cfg.ENGINE_CONTEXT_KEEP_TOOL_RESULTS
        out["clearAtLeastTokens"] = cfg.ENGINE_CONTEXT_CLEAR_AT_LEAST_TOKENS
        out["excludeTools"] = list(CLEAR_EXCLUDE_TOOLS)
    if rank >= 3:
        out["structuredSummary"] = True
        out["archiveSummarized"] = True
    return out


async def resolve_context_tiers(db: Any, tenant_id: Any) -> str:
    """The tenant's context tier (flag over global); ``off`` without a database or when the lookup fails."""
    from app.models.tenant import Tenant
    from app.services.feature_flags import context_tiers_for

    tenant = None
    if db is not None:
        try:
            tenant = await db.get(Tenant, uuid.UUID(str(tenant_id)))
        except Exception:
            logger.warning("tenant lookup for context tiers failed; using the global setting", exc_info=True)
    return context_tiers_for(tenant)


def model_id_variants(model: str) -> List[str]:
    """A model id as the policy may report it: as resolved, provider prefix stripped, OpenRouter form."""
    from app.services.llm_gateway import strip_provider_prefix, to_openrouter_model_id

    variants = [model]
    for fn in (strip_provider_prefix, to_openrouter_model_id):
        try:
            variants.append(fn(model))
        except Exception:  # an odd id never blocks a config build
            pass
    return list(dict.fromkeys(v for v in variants if v))


def windows_for_models(models: Iterable[str]) -> Dict[str, int]:
    """Context window per model id, for the ``contextWindows`` setting."""
    from app.config.llm_pricing import context_window_for

    out: Dict[str, int] = {}
    for model in models:
        if not model:
            continue
        window = context_window_for(model)
        for key in model_id_variants(model):
            out[key] = window
    return out


async def resolve_context_windows(db: Any, tenant_id: Any) -> Dict[str, int]:
    """Windows of the models the tenant's high, mid and low tiers resolve to (empty on any failure).

    Chat names no model (the policy picks one per request), so the windows of every model it can pick are
    given and the engine looks the chosen one up. A follow-up may return the window from the policy instead.
    """
    from app.services import llm_gateway as gw

    try:
        models = [await gw.resolve_model_for_tier(db, tenant_id, t, None) for t in ("high", "mid", "low")]
        return windows_for_models(models)
    except Exception:
        logger.warning("context window lookup failed; the model policy supplies windows", exc_info=True)
        return {}


CONTINUATION_HEADING = "Earlier conversation summary"


class AgentUnavailable(Exception):
    """The requested chat agent is missing, inactive or not visible to the user."""


class Selections(BaseModel):
    """What the user picked in the chat UI. Snake case is stored; camel case is accepted."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    agent_id: Optional[str] = Field(default=None, validation_alias=AliasChoices("agent_id", "agentId"))
    skill_ids: List[str] = Field(default_factory=list, validation_alias=AliasChoices("skill_ids", "skillIds", "active_skill_ids"))
    connector_ids: List[str] = Field(default_factory=list, validation_alias=AliasChoices("connector_ids", "connectorIds", "active_connector_ids"))
    knowledge_base_ids: List[str] = Field(default_factory=list, validation_alias=AliasChoices("knowledge_base_ids", "knowledgeBaseIds", "active_knowledge_base_ids"))
    web_search: bool = Field(default=False, validation_alias=AliasChoices("web_search", "webSearch", "web_search_enabled"))
    specific_sites: List[str] = Field(default_factory=list, validation_alias=AliasChoices("specific_sites", "specificSites"))


@dataclass
class ChatInputs:
    """Everything loaded from the database that the config depends on."""

    base_prompt: str
    agent_skills: List[str] = field(default_factory=list)
    agent_knowledge_base_ids: List[str] = field(default_factory=list)
    agent_id: Optional[str] = None
    project_name: Optional[str] = None
    project_instructions: Optional[str] = None
    project_files: List[str] = field(default_factory=list)
    project_knowledge_base_id: Optional[str] = None
    # Subsets of the requested ids the user may use (RBAC / existence), as strings.
    accessible_knowledge_base_ids: Set[str] = field(default_factory=set)
    valid_connector_ids: Set[str] = field(default_factory=set)


@dataclass
class BuiltAgent:
    agent: Dict[str, Any]  # the engine agent config (camelCase, engine schema)
    selections: Dict[str, Any]  # normalized, stored on the conversation record
    dropped: Dict[str, List[str]]  # ids removed (no access / unknown), by kind


def _dedupe(items: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(i for i in items if i))


def skill_names(refs: Iterable[str], known: Iterable[str]) -> List[str]:
    """Skill names for names or the UUIDs the UI sends (uuid5 of the name, as legacy chat maps them)."""
    known = set(known)
    by_uuid = {str(uuid.uuid5(_SKILL_NS, n)): n for n in known}
    return _dedupe(by_uuid.get(r, r) for r in refs)


def build_capabilities_block(tools: Iterable[str], tiers: str = "off") -> str:
    """The capability block naming the tool families actually granted (U4 / R4, KTD4)."""
    granted = set(tools)
    lines = [
        f"- {label}: {', '.join(sorted(granted.intersection(names)))}"
        for label, names in CAPABILITY_FAMILIES
        if granted.intersection(names)
    ]
    listed = set().union(*(set(names) for _, names in CAPABILITY_FAMILIES))
    other = sorted(granted - listed)
    if other:
        lines.append(f"- other tools: {', '.join(other)}")
    body = "\n".join(lines) if lines else "You have no tools beyond your own knowledge."
    if tier_rank(tiers) and "read_file" in granted:
        body += f"\n\n{OFFLOAD_NOTE}"
    return f"## {CAPABILITY_HEADING}\n\n{body}\n\n{CAPABILITY_FOOTER}"


def compose_agent_config(
    inputs: ChatInputs,
    selections: Selections,
    manifest_names: Iterable[str],
    now: Optional[datetime] = None,
    continuation_summary: Optional[str] = None,
    agent_role: Optional[str] = None,
    tiers: str = "off",
    context_windows: Optional[Dict[str, int]] = None,
) -> BuiltAgent:
    now = now or datetime.now(timezone.utc)
    manifest = set(manifest_names)
    dropped: Dict[str, List[str]] = {"knowledge_base_ids": [], "connector_ids": [], "skill_ids": []}

    requested_kbs = _dedupe(
        list(inputs.agent_knowledge_base_ids)
        + ([inputs.project_knowledge_base_id] if inputs.project_knowledge_base_id else [])
        + list(selections.knowledge_base_ids))
    kb_ids = [k for k in requested_kbs if k in inputs.accessible_knowledge_base_ids]
    dropped["knowledge_base_ids"] = [k for k in requested_kbs if k not in inputs.accessible_knowledge_base_ids]

    connector_ids = [c for c in _dedupe(selections.connector_ids) if c in inputs.valid_connector_ids]
    dropped["connector_ids"] = [c for c in _dedupe(selections.connector_ids) if c not in inputs.valid_connector_ids]

    wanted = skill_names(list(inputs.agent_skills) + list(selections.skill_ids), manifest)
    tools = {n for n in wanted if n in manifest}  # interactive-only skills are not in the manifest
    dropped["skill_ids"] = [n for n in wanted if n not in manifest]
    # The toggles below are authoritative for their tool families.
    tools -= set(WEB_TOOLS) | set(CONNECTOR_TOOLS) | {KNOWLEDGE_TOOL}
    if selections.web_search:
        tools |= {t for t in WEB_TOOLS if t in manifest}
    if connector_ids:
        tools |= {t for t in CONNECTOR_TOOLS if t in manifest}
    if kb_ids and KNOWLEDGE_TOOL in manifest:
        tools.add(KNOWLEDGE_TOOL)

    parts = [inputs.base_prompt]
    project_block = build_project_context(
        inputs.project_name, inputs.project_instructions, inputs.project_files)
    if project_block:
        parts.append(project_block)
    parts.append(f"[Context: Today's date is {now.strftime('%B %d, %Y')}.]")
    if kb_ids:
        parts.append(KNOWLEDGE_INSTRUCTION)
    if selections.web_search:
        parts.append(build_web_search_instruction(now, selections.specific_sites or None))
    if tier_rank(tiers):
        # Offloaded results are read back with read_file; a rewritten todo survives a summary (top level only).
        tools |= {t for t in ("read_file",) if t in manifest}
        if agent_role is None and TODO_TOOL in manifest:
            tools.add(TODO_TOOL)
    parts.append(build_capabilities_block(tools, tiers))
    # ask_user is native to the engine and only reaches a human in a top-level chat (U11): workers
    # (any ``agent_role``) have nobody to ask.
    if agent_role is None:
        tools.add(ASK_USER_TOOL)

    if continuation_summary and continuation_summary.strip():
        parts.append(
            f"## {CONTINUATION_HEADING}\n"
            "This conversation continues an earlier one. Use this summary as background; "
            "do not repeat it back unless asked.\n"
            "<earlier_conversation_summary>\n"
            f"{continuation_summary.strip()}\n"
            "</earlier_conversation_summary>")

    stored = {
        "agent_id": inputs.agent_id,
        "skill_ids": _dedupe(selections.skill_ids),
        "connector_ids": connector_ids,
        "knowledge_base_ids": kb_ids,
        "web_search": selections.web_search,
        "specific_sites": list(selections.specific_sites),
    }
    metadata: Dict[str, Any] = {
        "purpose": "chat",
        "allowedConnectorIds": connector_ids,
        "knowledgeBaseIds": kb_ids,
    }
    if inputs.agent_id:
        metadata["agentId"] = inputs.agent_id
    # No model id and no keys: the engine asks the policy endpoint per request.
    agent = {
        "systemPrompt": "\n\n".join(parts),
        "tools": sorted(tools),
        "compaction": compaction_settings(tiers=tiers, context_windows=context_windows),
        "metadata": metadata,
    }
    return BuiltAgent(agent=agent, selections=stored, dropped=dropped)


# --- database side ---------------------------------------------------------------


def _uuids(values: Iterable[str]) -> Dict[str, uuid.UUID]:
    out: Dict[str, uuid.UUID] = {}
    for v in values:
        try:
            out[str(v)] = uuid.UUID(str(v))
        except ValueError:
            continue
    return out


async def load_chat_inputs(
    db: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID, is_admin: bool,
    project_id: uuid.UUID, selections: Selections,
) -> ChatInputs:
    from app.models.connector import Connector
    from app.models.custom_agent import CustomAgent
    from app.models.knowledge_base import KnowledgeBase
    from app.models.knowledge_document import KnowledgeDocument
    from app.models.project import Project
    from app.models.team import TeamMembership

    if selections.agent_id:
        agent_uuid = _uuids([selections.agent_id]).get(selections.agent_id)
        row = None
        if agent_uuid is not None:
            row = (await db.execute(select(CustomAgent).where(
                CustomAgent.id == agent_uuid, CustomAgent.tenant_id == tenant_id))).scalar_one_or_none()
        if row is None or not row.is_active:
            raise AgentUnavailable("agent_not_found")
        if row.user_id != user_id:
            if row.visibility not in ("team", "tenant"):
                raise AgentUnavailable("agent_unauthorized")
            if row.visibility == "team" and row.team_id:
                member = (await db.execute(select(TeamMembership.id).where(
                    TeamMembership.team_id == row.team_id, TeamMembership.user_id == user_id)
                )).first()
                if member is None:
                    raise AgentUnavailable("agent_unauthorized")
        cfg = await resolve_agent_settings(row, db=db)
        base_prompt, agent_skills = cfg["system_prompt"], cfg["skill_names"]
        agent_kbs, agent_id = cfg["knowledge_base_ids"], str(row.id)
    else:
        from app.agents.general import GENERAL_AGENT_SYSTEM_PROMPT
        base_prompt, agent_skills, agent_kbs, agent_id = GENERAL_AGENT_SYSTEM_PROMPT, [], [], None

    inputs = ChatInputs(base_prompt=base_prompt, agent_skills=list(agent_skills or []),
                        agent_knowledge_base_ids=[str(k) for k in agent_kbs or []], agent_id=agent_id)

    proj = (await db.execute(select(Project).where(
        Project.id == project_id, Project.tenant_id == tenant_id))).scalar_one_or_none()
    if proj is not None:
        inputs.project_name = proj.name
        inputs.project_instructions = proj.instructions
        if proj.knowledge_base_id:
            inputs.project_knowledge_base_id = str(proj.knowledge_base_id)
            names = (await db.execute(
                select(KnowledgeDocument.filename).where(
                    KnowledgeDocument.knowledge_base_id == proj.knowledge_base_id,
                    KnowledgeDocument.tenant_id == tenant_id,
                    KnowledgeDocument.status == "ready").order_by(KnowledgeDocument.filename)))
            inputs.project_files = [n for (n,) in names.all()]

    wanted_kbs = _uuids(_dedupe(
        inputs.agent_knowledge_base_ids
        + ([inputs.project_knowledge_base_id] if inputs.project_knowledge_base_id else [])
        + list(selections.knowledge_base_ids)))
    if wanted_kbs:
        kbs = (await db.execute(select(KnowledgeBase.id, KnowledgeBase.team_id).where(
            KnowledgeBase.tenant_id == tenant_id, KnowledgeBase.id.in_(list(wanted_kbs.values()))))).all()
        team_ids = {t for _, t in kbs if t is not None}
        member_of: Set[uuid.UUID] = set()
        if team_ids and not is_admin:
            member_of = {t for (t,) in (await db.execute(select(TeamMembership.team_id).where(
                TeamMembership.user_id == user_id, TeamMembership.team_id.in_(team_ids)))).all()}
        for kb_id, team_id in kbs:
            # Team KBs need membership (tenant admins excepted); the project's own KB follows project access.
            if (team_id is None or is_admin or team_id in member_of
                    or str(kb_id) == inputs.project_knowledge_base_id):
                inputs.accessible_knowledge_base_ids.add(str(kb_id))

    wanted_connectors = _uuids(_dedupe(selections.connector_ids))
    if wanted_connectors:
        rows = (await db.execute(select(Connector.id).where(
            Connector.tenant_id == tenant_id, Connector.is_active == True,  # noqa: E712
            Connector.id.in_(list(wanted_connectors.values()))))).all()
        inputs.valid_connector_ids = {str(r[0]) for r in rows}
    return inputs


async def manifest_tool_names() -> Set[str]:
    """Names of the tools the gateway serves to the engine."""
    from app.internal_api.tools import build_manifest, ensure_skills_loaded

    await ensure_skills_loaded()
    return {t.name for t in build_manifest()}


async def build_engine_chat_agent(
    db: AsyncSession, *, tenant_id: Any, user: Any, project_id: Any, selections: Selections,
    loader: Callable[..., Any] = load_chat_inputs,
    manifest: Optional[Iterable[str]] = None,
    now: Optional[datetime] = None,
    continuation_summary: Optional[str] = None,
    agent_role: Optional[str] = None,
    tiers: Optional[str] = None,
) -> BuiltAgent:
    """Agent config for a chat conversation: raises ``AgentUnavailable`` for an unusable agent.

    ``tiers`` None resolves the context tier from the tenant flag and ``ENGINE_CONTEXT_TIERS`` (U16).
    """
    inputs = await loader(
        db, tenant_id=uuid.UUID(str(tenant_id)), user_id=uuid.UUID(str(user.id)),
        is_admin=getattr(user, "role", None) == "admin",
        project_id=uuid.UUID(str(project_id)), selections=selections)
    names = set(manifest) if manifest is not None else await manifest_tool_names()
    if tiers is None:
        tiers = await resolve_context_tiers(db, tenant_id)
    windows = await resolve_context_windows(db, tenant_id) if tier_rank(tiers) and db is not None else None
    return compose_agent_config(
        inputs, selections, names, now, continuation_summary, agent_role, tiers=tiers, context_windows=windows)
