"""Fleet agent config for the engine (fleet-workflows-on-engine plan, U6).

A pure function turns a ``CustomAgent`` into an engine agent config; the prompt, tool allowlist and
connector summary logic moved here from ``background_agent_executor`` (which re-exports them).
No key, tenant secret or DB handle ever enters the config; the engine asks the policy endpoint per
request. Tool names are filtered against the gateway manifest.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Budget defaults when resource_constraints is not configured
DEFAULT_MAX_TOOL_CALLS = 50
DEFAULT_MAX_DURATION_SEC = 1800  # 30 minutes

# Skills that require a chat session context and don't work in background execution
_SESSION_DEPENDENT_SKILLS = {"create_task", "update_task", "list_tasks"}

# When a batch version exists, suppress the individual version in background runs
# to force the LLM to use the more efficient batch call.
_BATCH_SUPERSEDES = {
    "sanctions_check": "batch_sanctions_check",
    "kyc_verification": "batch_kyc_verification",
}

_CONNECTOR_SKILLS = ("list_available_connectors", "invoke_connector_tool")


def _build_system_prompt(
    system_prompt: str,
    skill_names: List[str],
    connector_summary: Optional[str] = None,
) -> str:
    """Build a system prompt with tool usage instructions."""
    base = system_prompt or "You are a helpful AI assistant."
    tools_note = ""
    if skill_names:
        tools_note = (
            "\n\nYou have access to tools that will be provided in the conversation. "
            "Use them when appropriate to accomplish the task. "
            "Process ALL items before responding — do NOT give intermediate summaries "
            "after partial batches. When the task is fully complete, respond with a "
            "single clear summary of everything that was done. "
            "IMPORTANT: When a batch version of a tool exists (e.g. batch_sanctions_check, "
            "batch_kyc_verification), ALWAYS use it instead of calling the single-entity "
            "version repeatedly. Pass all entities in one call."
        )
    if connector_summary:
        tools_note += (
            f"\n\nAvailable connectors (no need to call list_available_connectors):\n"
            f"{connector_summary}"
        )
    return base + tools_note


def allowed_skill_names(skill_names: Iterable[str]) -> List[str]:
    """Skill names minus session-dependent ones and individuals superseded by a batch skill."""
    names = list(skill_names or [])
    suppress = {
        individual for individual, batch in _BATCH_SUPERSEDES.items()
        if individual in names and batch in names
    }
    out: List[str] = []
    for n in names:
        if n in _SESSION_DEPENDENT_SKILLS or n in suppress or n in out:
            continue
        out.append(n)
    return out


def _load_tools(skill_names: List[str]) -> Dict[str, Any]:
    """Load tool handlers from the skill registry.

    Filters out session-dependent skills (e.g. create_task) that require
    an active chat session and would fail in background execution.
    Also suppresses individual tools when a batch version is available.
    """
    from app.agents.skills.registry import SkillRegistry

    tools = {}
    for skill_name in allowed_skill_names(skill_names):
        skill = SkillRegistry.get_skill(skill_name)
        if skill and skill.handler:
            tools[skill_name] = skill
    return tools


async def _preload_connector_summary(user_id: str, tenant_id: str) -> Optional[str]:
    """Pre-resolve connector list so the agent can skip the discovery tool call."""
    try:
        from app.agents.skills.context import SkillContext
        from app.connectors.registry import ConnectorRegistry

        ctx = SkillContext(user_id=user_id, tenant_id=tenant_id)

        # Reuse the same connector loading logic as the skill
        from app.agents.skills.connectors import _get_connectors
        connectors = _get_connectors(ctx)
        if not connectors:
            return None

        lines = []
        for conn_id, conn_info in connectors.items():
            provider = conn_info.get("provider", "")
            connector_instance = ConnectorRegistry.get_connector(provider)
            tool_names = []
            if connector_instance:
                try:
                    raw_tools = connector_instance.get_tools()
                    tool_names = [t.get("name", "") for t in raw_tools]
                except Exception:
                    pass
            tools_str = ", ".join(tool_names) if tool_names else "none"
            lines.append(
                f"- connector_id=\"{conn_id}\" name=\"{conn_info.get('name', '')}\" "
                f"tools: [{tools_str}]"
            )
        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"Failed to preload connectors (non-critical): {e}")
        return None


def wants_connector_summary(skill_names: Iterable[str]) -> bool:
    return any(s in (skill_names or []) for s in _CONNECTOR_SKILLS)


def _manifest_names() -> List[str]:
    from app.internal_api.tools import build_manifest

    return [t.name for t in build_manifest()]


@dataclass
class FleetAgentConfig:
    config: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)


def _positive_int(value: Any, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def build_fleet_agent_config(
    agent: Any,
    job_id: Any,
    now_ms: int,
    connector_summary: Optional[str] = None,
    manifest_names: Optional[Iterable[str]] = None,
    manifest_loader: Callable[[], Iterable[str]] = _manifest_names,
) -> FleetAgentConfig:
    """Pure: ``CustomAgent`` -> engine agent config (+ warnings for dropped skills).

    ``now_ms`` is the run start in epoch ms; ``deadlineMs`` is absolute. ``manifest_names`` (or the
    gateway manifest) bounds the tool list; unknown skills are dropped with a warning.
    """
    skills = list(getattr(agent, "skills", None) or [])
    constraints = getattr(agent, "resource_constraints", None) or {}
    allowed = allowed_skill_names(skills)
    known = set(manifest_names if manifest_names is not None else manifest_loader())
    tools = [n for n in allowed if n in known]
    warnings = [f"skill '{n}' is not in the tool manifest and was dropped" for n in allowed if n not in known]

    max_rounds = _positive_int(constraints.get("max_tool_calls"), DEFAULT_MAX_TOOL_CALLS)
    duration_sec = _positive_int(constraints.get("timeout_seconds"), DEFAULT_MAX_DURATION_SEC)

    config: Dict[str, Any] = {
        "systemPrompt": _build_system_prompt(
            getattr(agent, "system_prompt", None) or "", tools, connector_summary),
        "tools": tools,
        "maxRounds": max_rounds,
        "deadlineMs": int(now_ms) + duration_sec * 1000,
        "metadata": {"notify": "backend", "runKind": "fleet", "jobId": str(job_id)},
    }
    model = getattr(agent, "model", None)
    if model:
        config["model"] = model
    tier = (getattr(agent, "config", None) or {}).get("tier") if isinstance(
        getattr(agent, "config", None), dict) else None
    if tier:
        config["tier"] = tier
    return FleetAgentConfig(config=config, warnings=warnings)
