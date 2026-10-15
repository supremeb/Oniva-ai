"""Tool gateway: the manifest route (U3). Invoke and reconcile arrive in U5."""
import logging
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from fastapi import APIRouter

from app.agents.skills.registry import SkillRegistry, _manifests, _registry
from app.internal_api.schemas import ManifestResponse, ManifestTool, build_parameters_schema
from app.internal_api.tool_specs import is_declared, spec_for

logger = logging.getLogger(__name__)

SIMULATED_PREFIX = "[simulated] "
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

router = APIRouter(prefix="/tools")


async def ensure_skills_loaded() -> None:
    """Load SKILL.md skills and import every legacy skill module (both idempotent)."""
    await SkillRegistry.initialize()
    for name in list(_manifests):
        SkillRegistry.load_skill(name)


def loaded_skill_names() -> List[str]:
    return sorted(_registry)


def undeclared_skills() -> List[str]:
    """Loaded skills with no ToolSpec."""
    return [n for n in loaded_skill_names() if not is_declared(n)]


def _explicit_timeouts() -> Dict[str, int]:
    """`timeout-seconds` set explicitly in a SKILL.md (the loader's 60 s default is not an override)."""
    out: Dict[str, int] = {}
    for md in SKILLS_DIR.glob("*/SKILL.md"):
        try:
            text = md.read_text()
            if not text.startswith("---"):
                continue
            front = yaml.safe_load(text.split("---", 2)[1]) or {}
            seconds = front.get("timeout-seconds")
            if isinstance(seconds, int) and seconds > 0:
                out[str(front.get("name", md.parent.name))] = seconds * 1000
        except Exception:
            logger.warning("Could not read timeout from %s", md, exc_info=True)
    return out


def build_manifest() -> List[ManifestTool]:
    """Manifest entries for every loaded, declared-for-export skill, sorted by name."""
    timeouts = _explicit_timeouts()
    tools: List[ManifestTool] = []
    for name in loaded_skill_names():
        skill = _registry[name]
        spec = spec_for(name)
        if not spec.in_manifest:
            continue
        manifest: Optional[object] = _manifests.get(name)
        description = skill.description or name
        if getattr(manifest, "mock", False):
            description = SIMULATED_PREFIX + description
        tools.append(
            ManifestTool(
                name=name,
                description=description,
                parameters=build_parameters_schema(skill.handler, name),
                sideEffect=spec.side_effect,
                idempotent=spec.idempotent,
                timeoutMs=timeouts.get(name, spec.timeout_ms),
                requiresApproval=spec.requires_approval,
            )
        )
    return tools


@router.get("/manifest", response_model=ManifestResponse)
async def get_manifest() -> ManifestResponse:
    # U5 wires fence admission (X-Engine-Fence / X-Engine-Session) in front of this.
    await ensure_skills_loaded()
    return ManifestResponse(tools=build_manifest())
