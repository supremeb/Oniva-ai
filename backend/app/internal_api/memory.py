"""Memory curator gateway (plan 2026-10-21-001, U31, KTD18): ``/internal/memory/curate/{prepare,apply}``.

The contract is the engine's (``engine/src/memory/contract.ts``; the clauses are tagged in
``engine/test/support/fake-memory-backend.ts`` and checked by ``tests/internal/test_contract_conformance.py``).
Both routes are keyed ``curate:{conversationId}:{runId}:{phase}``: ``conversationId`` and ``runId`` name the
SOURCE run being curated, and the tenant and project are those of that conversation's binding, never the body's.
A key with a stored outcome is answered from the store and never re-run (a stored skip included), whatever
the repeat's body says: the key is the call's identity (``args_hash`` covers only the ids it encodes); a started call
runs to completion even if the client disconnects. Auth, fence and the outcome store are the tool gateway's
(``tool_invocations`` is keyed by the same unique ``Idempotency-Key`` column). All semantics are in
``app.services.memory_curator``.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.core.database import get_db_for_tenant
from app.internal_api import invocations
from app.internal_api.fence import (
    GatewayError,
    SessionFactory,
    admit_fence,
    fence_headers,
    hold_write_fence,
    recheck_fence,
)
from app.internal_api.invocations import get_session_factory
from app.internal_api.tools import _detach
from app.services import memory_curator as mc
from app.services.engine_client import get_engine_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/memory/curate")

CONTRACT_VERSION = 1
MAX_ID = 256
MAX_CTX = 4096
MAX_ANSWER = 131_072
PHASE_TIMEOUT_MS = {"prepare": 120_000, "apply": 60_000}
PHASE_TOOL = {"prepare": mc.TOOL_PREPARE, "apply": mc.TOOL_APPLY}


def get_tenant_session_factory() -> Callable[[str], Any]:
    """FastAPI dependency: tenant-scoped sessions (tests override it)."""
    return get_db_for_tenant


def get_engine() -> Any:
    """FastAPI dependency: the engine client (tests override it)."""
    return get_engine_client()


def get_curator_settings() -> Settings:
    """FastAPI dependency: settings (tests override it)."""
    return get_settings()


class InvalidRequest(ValueError):
    pass


def _text(o: Dict[str, Any], key: str, limit: int, *, allow_empty: bool = False) -> str:
    v = o.get(key)
    if not isinstance(v, str):
        raise InvalidRequest(f"{key} must be a string")
    if not allow_empty and not v:
        raise InvalidRequest(f"{key} must not be empty")
    if len(v) > limit:
        raise InvalidRequest(f"{key} is longer than {limit} characters")
    return v


def validate(phase: str, raw: Any) -> Dict[str, str]:
    """The request body as the engine's ``parseCurate{Prepare,Apply}Request`` reads it (unknown keys ignored)."""
    if not isinstance(raw, dict):
        raise InvalidRequest("body must be an object")
    version = raw.get("contractVersion")
    if type(version) is not int or version != CONTRACT_VERSION:
        raise InvalidRequest(f"contractVersion must be {CONTRACT_VERSION}")
    out = {"conversationId": _text(raw, "conversationId", MAX_ID), "runId": _text(raw, "runId", MAX_ID)}
    if phase == "prepare":
        out["curateConversationId"] = _text(raw, "curateConversationId", MAX_ID)
        out["curateTaskId"] = _text(raw, "curateTaskId", MAX_ID)
    else:
        out["ctx"] = _text(raw, "ctx", MAX_CTX, allow_empty=True)
        out["answer"] = _text(raw, "answer", MAX_ANSWER)
    return out


def _no_store(content: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(content, status_code=status, headers={"Cache-Control": "no-store"})


def _bad_key(phase: str) -> GatewayError:
    return GatewayError(400, {"error": "invalid_idempotency_key",
                              "message": f"expected curate:{{conversationId}}:{{runId}}:{phase}"})


def _check_key(request: Request, raw: Any, phase: str) -> str:
    key = request.headers.get("idempotency-key") or ""
    obj = raw if isinstance(raw, dict) else {}
    conv, run = obj.get("conversationId"), obj.get("runId")
    if (not isinstance(conv, str) or not conv or not isinstance(run, str) or not run
            or key != f"curate:{conv}:{run}:{phase}" or len(key) > 255):
        raise _bad_key(phase)
    return key


def _stored_response(phase: str, stored: Dict[str, Any]) -> Dict[str, Any]:
    """A stored outcome as a contract body; the startup sweep's tool-shaped ``interrupted`` becomes a skip."""
    if "contractVersion" in stored:
        return stored
    return mc.skip("interrupted") if phase == "prepare" else mc.skipped_apply("interrupted")


async def _perform(
    factory: SessionFactory, tenants: Callable[[str], Any], engine: Any, settings: Settings, *,
    phase: str, key: str, session_id: str, fence_token: Optional[int], binding: mc.Binding, body: Dict[str, str],
) -> Dict[str, Any]:
    """Run the effect (under the write fence for ``apply``), store its outcome. Runs detached.

    Any failure releases the claim: ``apply`` is one transaction, and ``prepare`` has no effect, so a retry
    under the same key is safe.
    """
    try:
        if fence_token is None:
            guard: Any = nullcontext()
        elif phase == "apply":
            guard = hold_write_fence(factory, session_id, fence_token)
        else:
            await recheck_fence(factory, session_id, fence_token)
            guard = nullcontext()
        async with guard:
            if phase == "prepare":
                outcome = await mc.prepare(
                    factory=factory, tenants=tenants, engine=engine, settings=settings, binding=binding,
                    conversation_id=body["conversationId"], run_id=body["runId"])
            else:
                outcome = await mc.apply(
                    tenants=tenants, settings=settings, binding=binding, conversation_id=body["conversationId"],
                    run_id=body["runId"], ctx=body["ctx"], answer=body["answer"])
            if not await invocations.store_outcome(factory, key, str(binding.tenant_id), outcome):
                logger.warning("Outcome for %s was not stored: the claim is no longer in flight", key)
            return outcome
    except BaseException:
        await invocations.release_claim(factory, key, str(binding.tenant_id))
        raise


async def _handle(
    request: Request, phase: str, factory: SessionFactory, tenants: Callable[[str], Any], engine: Any,
    settings: Settings,
) -> JSONResponse:
    try:
        raw = await request.json()
    except ValueError:
        raise GatewayError(400, {"error": "invalid_request", "message": "body is not JSON"})
    # Like the engine's fake, a stale fence is answered before the key and the body are judged.
    token, header_session = fence_headers(request)
    if token is not None and header_session:
        await admit_fence(factory, header_session, token)
    key = _check_key(request, raw, phase)
    try:
        body = validate(phase, raw)
    except InvalidRequest:
        raise GatewayError(422, {"error": "invalid_request"})

    binding = await mc.load_binding(
        factory, body["conversationId"], curator_id=body.get("curateConversationId"), run_id=body["runId"])
    session_id = header_session or f"{binding.tenant_id}/{binding.project_id}"
    if token is not None and not header_session:
        await admit_fence(factory, session_id, token)

    started = await invocations.begin(
        factory, key=key, tenant_id=str(binding.tenant_id), session_id=session_id, tool=PHASE_TOOL[phase],
        arguments_hash=invocations.args_hash({"conversationId": body["conversationId"], "runId": body["runId"]}),
        fence_token=token, timeout_ms=PHASE_TIMEOUT_MS[phase],
        idempotent=True,
    )
    if started.kind == "stored":
        return _no_store(_stored_response(phase, started.outcome or {}))
    if started.kind == "busy":
        raise GatewayError(503, {"error": "in_flight"}, {"Retry-After": "1"})

    task = _detach(_perform(
        factory, tenants, engine, settings, phase=phase, key=key, session_id=session_id, fence_token=token,
        binding=binding, body=body))
    return _no_store(await asyncio.shield(task))


@router.post("/prepare")
async def prepare_route(
    request: Request,
    factory: SessionFactory = Depends(get_session_factory),
    tenants: Callable[[str], Any] = Depends(get_tenant_session_factory),
    engine: Any = Depends(get_engine),
    settings: Settings = Depends(get_curator_settings),
) -> JSONResponse:
    return await _handle(request, "prepare", factory, tenants, engine, settings)


@router.post("/apply")
async def apply_route(
    request: Request,
    factory: SessionFactory = Depends(get_session_factory),
    tenants: Callable[[str], Any] = Depends(get_tenant_session_factory),
    engine: Any = Depends(get_engine),
    settings: Settings = Depends(get_curator_settings),
) -> JSONResponse:
    return await _handle(request, "apply", factory, tenants, engine, settings)
