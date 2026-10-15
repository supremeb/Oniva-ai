"""Tool gateway routes: manifest (U3), invoke, idempotency and reconcile (U5)."""
import asyncio
import logging
import uuid
from contextlib import nullcontext
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from app.agents.skills.registry import SkillRegistry, _manifests, _registry
from app.internal_api import invocations
from app.internal_api.execution import ToolRateLimited, execute_tool
from app.internal_api.fence import (
    FenceBusy,
    FenceRejected,
    GatewayError,
    SessionFactory,
    admit_fence,
    fence_headers,
    hold_write_fence,
    recheck_fence,
)
from app.internal_api.invocations import get_session_factory
from app.internal_api.schemas import (
    ManifestResponse,
    ManifestTool,
    build_args_model,
    build_parameters_schema,
)
from app.internal_api.tool_specs import TOOL_SPECS, ToolSpec, is_declared, spec_for

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


@lru_cache(maxsize=1)
def _cached_timeouts() -> Dict[str, int]:
    return _explicit_timeouts()


def effective_timeout_ms(name: str, spec: Optional[ToolSpec] = None) -> int:
    return _cached_timeouts().get(name, (spec or spec_for(name)).timeout_ms)


def idempotent_tool_names() -> Set[str]:
    return {n for n in TOOL_SPECS if TOOL_SPECS[n].idempotent}


def stale_after_seconds() -> int:
    """How old an in-flight claim must be before the startup sweep may interrupt it."""
    longest = max([s.timeout_ms for s in TOOL_SPECS.values()] + list(_cached_timeouts().values()))
    return longest // 1000 + invocations.STALE_GRACE_S


def _no_store(body: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


@router.get("/manifest", response_model=ManifestResponse)
async def get_manifest(request: Request, factory: SessionFactory = Depends(get_session_factory)) -> ManifestResponse:
    token, session = fence_headers(request)
    if token is not None and session is not None:
        await admit_fence(factory, session, token)  # a manifest load raises the mark too
    await ensure_skills_loaded()
    return ManifestResponse(tools=build_manifest())


# --- invoke ---------------------------------------------------------------------


class InvokeBody(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    args: Dict[str, Any]
    runId: str
    toolCallId: str
    tenantId: str
    projectId: str
    conversationId: Optional[str] = None
    # The engine conversation that made the call (a subagent's child); informational only.
    engineConversationId: Optional[str] = None


# Strong references: a detached effect must outlive the request that started it (KTD6).
_effects: Set["asyncio.Task[Any]"] = set()


def _detach(coro) -> "asyncio.Task[Any]":
    task = asyncio.create_task(coro)
    _effects.add(task)

    def _done(t: "asyncio.Task[Any]") -> None:
        _effects.discard(t)
        if not t.cancelled():
            t.exception()  # consumed here; the awaiting request (if any) re-raises it

    task.add_done_callback(_done)
    return task


async def _perform(
    factory: SessionFactory,
    *,
    name: str,
    spec: ToolSpec,
    body: InvokeBody,
    key: str,
    session_id: str,
    fence_token: Optional[int],
    user_id: uuid.UUID,
    timeout_ms: int,
    bindings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the effect under the write fence, store its outcome, release the lock. Runs detached."""
    effect_started = False
    try:
        if fence_token is None:
            guard = nullcontext()
        elif spec.side_effect == "write":
            guard = hold_write_fence(factory, session_id, fence_token)
        else:
            await recheck_fence(factory, session_id, fence_token)
            guard = nullcontext()
        async with guard:
            effect_started = True
            outcome = await execute_tool(
                name,
                body.args,
                tenant_id=body.tenantId,
                project_id=body.projectId,
                user_id=user_id,
                conversation_id=body.conversationId,
                run_id=body.runId,
                tool_call_id=body.toolCallId,
                bindings=bindings,
                spec=spec,
                timeout_ms=timeout_ms,
            )
            stored = invocations.outcome_to_dict(outcome)
            if not await invocations.store_outcome(factory, key, body.tenantId, stored):
                logger.warning("Outcome for %s was not stored: the claim is no longer in flight", key)
            return stored
    except (FenceRejected, FenceBusy, ToolRateLimited):
        await invocations.release_claim(factory, key, body.tenantId)  # nothing ran
        raise
    except Exception:
        if not effect_started:
            await invocations.release_claim(factory, key, body.tenantId)
        raise


@router.post("/{name}/invoke")
async def invoke_tool(name: str, request: Request, factory: SessionFactory = Depends(get_session_factory)) -> JSONResponse:
    await ensure_skills_loaded()
    if name not in _registry or not spec_for(name).in_manifest:
        raise GatewayError(404, {"error": "unknown_tool"})
    spec = spec_for(name)

    try:
        body = InvokeBody.model_validate(await request.json())
    except (ValueError, ValidationError):
        raise GatewayError(400, {"error": "invalid_request"})
    key = request.headers.get("idempotency-key")
    if key != f"{body.runId}:{body.toolCallId}" or len(key) > 255:
        raise GatewayError(400, {"error": "invalid_request"})
    try:
        build_args_model(_registry[name].handler, name).model_validate(body.args)
    except ValidationError as exc:
        problems = [{"path": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
        raise GatewayError(400, {"error": "invalid_request", "problems": problems})

    token, header_session = fence_headers(request)
    session_id = header_session or f"{body.tenantId}/{body.projectId}"
    if token is not None:
        await admit_fence(factory, session_id, token)

    user_id = await invocations.resolve_binding(factory, body.tenantId, body.projectId, body.conversationId)

    bindings: Optional[Dict[str, Any]] = None
    if name == "search_knowledge":
        # KB scope comes from the conversation record, never from the model (KTD3).
        kb_ids = await invocations.conversation_knowledge_bases(factory, body.tenantId, body.conversationId)
        if kb_ids is not None:
            bindings = {"knowledge_base_ids": kb_ids}

    denied = await invocations.connector_denial(factory, body.tenantId, body.conversationId, name, body.args)
    if denied is not None:
        return _no_store(denied)  # a tool error result, not an HTTP error; nothing is claimed

    timeout_ms = effective_timeout_ms(name, spec)
    started = await invocations.begin(
        factory,
        key=key,
        tenant_id=body.tenantId,
        session_id=session_id,
        tool=name,
        arguments_hash=invocations.args_hash(body.args),
        fence_token=token,
        timeout_ms=timeout_ms,
        idempotent=spec.idempotent,
    )
    if started.kind == "stored":
        return _no_store(started.outcome)
    if started.kind == "busy":
        raise GatewayError(503, {"error": "in_flight"}, {"Retry-After": "1"})

    # Detached and shielded: a client that disconnects does not cancel a started effect.
    task = _detach(
        _perform(
            factory, name=name, spec=spec, body=body, key=key, session_id=session_id,
            fence_token=token, user_id=user_id, timeout_ms=timeout_ms, bindings=bindings,
        )
    )
    return _no_store(await asyncio.shield(task))


# --- reconcile ------------------------------------------------------------------


@router.get("/invocations/{key}")
async def get_invocation(key: str, request: Request, factory: SessionFactory = Depends(get_session_factory)) -> JSONResponse:
    token, session = fence_headers(request)
    if token is not None and session is not None:
        # Waits for an older owner's in-flight write, so a 404 below is final (KTD4).
        await admit_fence(factory, session, token)
    stored = await invocations.completed_outcome(factory, key, session)
    if stored is None:
        raise GatewayError(404, {"error": "not_found"})
    return _no_store(stored)
