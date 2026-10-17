"""Workflow node gateway (plan 2026-10-17-003, U13, KTD4): ``/internal/workflows/...``.

``prepare`` and ``finish`` are keyed ``{runId}:{nodeId}:r{runAttempt}:a{nodeAttempt}:{phase}``; a key
with a stored outcome is answered from the store and never re-run, so a replayed engine call is
exactly-once. ``runAttempt`` is part of the key: a run retry re-executes a node whose ``r0`` key stored
a failure. Auth, fence and the outcome store are the tool gateway's (``tool_invocations`` is keyed by the
same unique ``Idempotency-Key`` column). Bodies are validated by the U33 models.

The ``finalize`` route belongs to U16.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, AsyncContextManager, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.database import get_db_for_tenant
from app.internal_api import invocations, workflow_contract as wc
from app.internal_api.fence import (
    GatewayError,
    SessionFactory,
    admit_fence,
    fence_headers,
    hold_write_fence,
)
from app.internal_api.invocations import get_session_factory
from app.internal_api.node_specs import node_spec_for
from app.internal_api.tools import _detach
from app.models.engine import EngineConversation
from app.models.workflow import WorkflowExecution
from app.node_executors import NodeExecutionContext, NodeExecutionResult, get_executor
from app.node_executors.adornments import CacheAdornment
from app.schemas.classification import DataClassification
from app.services.workflow_compiler import UNEXECUTED_TYPES, compile_workflow, resolve_spec

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workflows")

TenantSessionFactory = Callable[[str], AsyncContextManager[Any]]

KEY_RE = re.compile(r"^(?P<run>[^:\s]+):(?P<node>[^:\s]+):r(?P<r>\d+):a(?P<a>\d+):(?P<phase>prepare|finish)$")
SCOPE_KEYS = ("tenantId", "projectId", "conversationId", "classification", "nodeType")
PHASE_TOOL = {"prepare": "workflow_prepare", "finish": "workflow_finish"}
INTERRUPTED_FINAL: Dict[str, Any] = {
    "contractVersion": wc.CONTRACT_VERSION,
    "kind": "final",
    "status": "failed",
    "output": None,
    "tokensUsed": 0,
    "error": "The call was interrupted before its outcome was recorded.",
}


def get_tenant_session_factory() -> TenantSessionFactory:
    """FastAPI dependency: tenant-scoped sessions for executors and run data (tests override it)."""
    return get_db_for_tenant


class Scope(BaseModel):
    """The run's binding. Tenant, project and conversation are derived from the run (KTD4); a body that
    names them must agree with the run. ``classification`` and ``nodeType`` are optional hints."""

    model_config = ConfigDict(extra="forbid")

    tenantId: str
    projectId: str
    conversationId: Optional[str] = None
    classification: Optional[str] = None
    nodeType: Optional[str] = None


class BodyScope(BaseModel):
    """Scope keys as the body may carry them: all optional, none trusted."""

    model_config = ConfigDict(extra="forbid")

    tenantId: Optional[str] = None
    projectId: Optional[str] = None
    conversationId: Optional[str] = None
    classification: Optional[str] = None
    nodeType: Optional[str] = None


def _bad(message: str = "") -> GatewayError:
    body: Dict[str, Any] = {"error": "invalid_request"}
    if message:
        body["message"] = message
    return GatewayError(400, body)


def _uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise _bad(f"{what} is not a UUID")


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _no_store(content: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(content, status_code=status, headers={"Cache-Control": "no-store"})


def _final(
    status: str,
    output: Any = None,
    *,
    error: Optional[str] = None,
    tokens: int = 0,
    classification: Optional[DataClassification] = None,
    handles: Optional[List[str]] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "contractVersion": wc.CONTRACT_VERSION,
        "kind": "final",
        "status": status,
        "output": _json_safe(output),
        "tokensUsed": int(tokens or 0),
    }
    if error is not None:
        body["error"] = error
    if classification is not None:
        body["classification"] = classification.value
    if handles:
        body["selectedHandles"] = list(handles)
    return body


def _result_to_final(result: NodeExecutionResult, input_class: DataClassification) -> Dict[str, Any]:
    classification = max(result.classification, input_class)
    body = _final(
        result.status,
        result.output,
        error=result.error,
        tokens=result.tokens_used or 0,
        classification=classification,
        handles=[result.next_node_id] if result.next_node_id else None,
    )
    try:
        return wc.validate_prepare_response(body).model_dump(exclude_unset=True)
    except wc.ContractError as exc:
        return _final("failed", None, error=f"node output rejected: {exc.code}", classification=classification)


# --- request parsing -----------------------------------------------------------------------------


def parse_key(request: Request, run_id: str, node_id: str, phase: str) -> re.Match:
    key = request.headers.get("idempotency-key") or ""
    m = KEY_RE.match(key)
    if not m or len(key) > 255 or m["phase"] != phase or m["run"] != run_id or m["node"] != node_id:
        raise GatewayError(
            400,
            {
                "error": "invalid_idempotency_key",
                "message": f"expected {{runId}}:{{nodeId}}:r{{n}}:a{{n}}:{phase}",
            },
        )
    return m


def _parse_class(value: Optional[str]) -> DataClassification:
    if value is None:
        return DataClassification.INTERNAL
    try:
        return DataClassification(value)
    except ValueError:
        raise _bad("unknown classification")


async def _read_body(request: Request) -> Dict[str, Any]:
    try:
        raw = await request.json()
    except ValueError:
        raise _bad("body is not JSON")
    if not isinstance(raw, dict):
        raise _bad("body must be an object")
    return raw


def _split_scope(raw: Dict[str, Any]) -> tuple[BodyScope, Dict[str, Any]]:
    try:
        scope = BodyScope(**{k: raw[k] for k in SCOPE_KEYS if k in raw})
    except ValidationError:
        raise _bad("scope fields must be strings")
    return scope, {k: v for k, v in raw.items() if k not in SCOPE_KEYS}


async def bind_run(factory: SessionFactory, run: uuid.UUID, claimed: BodyScope) -> Scope:
    """The tenant, project and conversation of ``run``, from the run itself (KTD4).

    The body's tenantId/projectId/conversationId are never trusted: when present they must equal what the
    run says, else the run is not found for that caller (404). A run with no conversation takes its
    project from the body, verified against the tenant by ``resolve_binding``.
    """
    async with factory() as db:
        execution = (
            await db.execute(
                select(WorkflowExecution.tenant_id, WorkflowExecution.engine_conversation_id).where(
                    WorkflowExecution.id == run
                )
            )
        ).first()
        if execution is None:
            raise GatewayError(404, {"error": "run_not_found"})
        conv = (
            await db.execute(
                select(EngineConversation.conversation_id, EngineConversation.project_id)
                .where(
                    EngineConversation.tenant_id == execution.tenant_id,
                    or_(
                        EngineConversation.conversation_id == execution.engine_conversation_id,
                        (EngineConversation.run_kind == "workflow") & (EngineConversation.run_ref_id == run),
                    ),
                )
                .limit(1)
            )
        ).first()
    tenant = str(execution.tenant_id)
    conversation = str(conv.conversation_id) if conv is not None else None
    project = str(conv.project_id) if conv is not None else claimed.projectId
    if claimed.tenantId is not None and claimed.tenantId != tenant:
        raise GatewayError(404, {"error": "run_not_found"})
    if claimed.projectId is not None and project is not None and claimed.projectId != project:
        raise GatewayError(404, {"error": "run_not_found"})
    if claimed.conversationId is not None and claimed.conversationId != conversation:
        raise GatewayError(404, {"error": "run_not_found"})
    if project is None:
        raise GatewayError(404, {"error": "conversation_not_found"})
    return Scope(
        tenantId=tenant,
        projectId=project,
        conversationId=conversation,
        classification=claimed.classification,
        nodeType=claimed.nodeType,
    )


# --- run data ------------------------------------------------------------------------------------


class RunView:
    """Everything the effect needs, loaded once under the tenant session."""

    def __init__(self, execution: WorkflowExecution, spec: Dict[str, Any], node: Dict[str, Any]) -> None:
        self.execution_id: uuid.UUID = execution.id
        self.workflow_id = execution.workflow_id
        self.user_id = execution.user_id
        self.tenant_id: uuid.UUID = execution.tenant_id
        self.trigger = execution.trigger
        self.variables: Dict[str, Any] = dict(execution.input_data or {})
        self.trace: List[Dict[str, Any]] = list(execution.execution_trace or [])
        self.spec = spec
        self.node = node


async def load_run(
    tenants: TenantSessionFactory, tenant: uuid.UUID, run: uuid.UUID, node_id: str
) -> RunView:
    async with tenants(str(tenant)) as db:
        execution = (
            await db.execute(
                select(WorkflowExecution).where(WorkflowExecution.id == run, WorkflowExecution.tenant_id == tenant)
            )
        ).scalar_one_or_none()
        if execution is None:
            raise GatewayError(404, {"error": "run_not_found"})
        pinned = (execution.input_data or {}).get("workflow_version_id")
        spec = None
        if execution.workflow_id is not None:
            spec = await resolve_spec(db, tenant, execution.workflow_id, uuid.UUID(pinned) if pinned else None)
        if spec is None:
            raise GatewayError(404, {"error": "workflow_not_found"})
        node = next((n for n in spec.get("nodes", []) if n.get("id") == node_id), None)
        if node is None:
            raise GatewayError(404, {"error": "node_not_found"})
        return RunView(execution, spec, node)


def predecessor_inputs(view: RunView, run_attempt: int) -> Dict[str, Any]:
    """Outputs of this node's live in-edge sources, from the stored trace (never the engine)."""
    compiled = compile_workflow(view.spec, validate=False)
    entry = next((n for n in compiled["nodes"] if n["id"] == view.node["id"]), None)
    sources = {d["from"] for d in (entry["deps"] if entry else [])}
    inputs: Dict[str, Any] = {}
    for step in view.trace:
        if step.get("node_id") in sources and step.get("status") == "success" and step.get("run_attempt", 0) <= run_attempt:
            inputs[step["node_id"]] = step.get("output")  # trace order: later attempts overwrite earlier
    return inputs


async def record_trace(
    tenants: TenantSessionFactory, tenant: uuid.UUID, run: uuid.UUID, step: Dict[str, Any]
) -> None:
    """Upsert one trace step keyed by ``(node_id, run_attempt, attempt)``; a replay does not duplicate it."""
    async with tenants(str(tenant)) as db:
        execution = (
            await db.execute(
                select(WorkflowExecution)
                .where(WorkflowExecution.id == run, WorkflowExecution.tenant_id == tenant)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if execution is None:
            return
        trace = [dict(s) for s in (execution.execution_trace or [])]
        ident = (step["node_id"], step["run_attempt"], step["attempt"])
        for i, existing in enumerate(trace):
            if (existing.get("node_id"), existing.get("run_attempt"), existing.get("attempt")) == ident:
                trace[i] = step
                break
        else:
            trace.append(step)
        execution.execution_trace = trace
        execution.nodes_executed = len({s["node_id"] for s in trace})
        execution.total_tokens = sum(int(s.get("tokens_used") or 0) for s in trace)
        await db.commit()


def _insert_for(db: Any):
    return (pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert)(WorkflowExecution)


def item_id(run: uuid.UUID, node_id: str, index: int) -> uuid.UUID:
    return uuid.uuid5(run, f"{node_id}:{index}")


# --- prepare -------------------------------------------------------------------------------------


async def _prepare_fanout(
    tenants: TenantSessionFactory, view: RunView, context: NodeExecutionContext, kind: str
) -> Dict[str, Any]:
    data = view.node.get("data") or {}
    if kind == "iteration":
        executor = get_executor("iteration")
        items = executor._get_array_value(data.get("inputArray", ""), context)  # type: ignore[union-attr]
        if len(items) > wc.MAX_FANOUT_ITEMS:
            return _final(
                "failed",
                None,
                error=f"Input array size ({len(items)}) exceeds maximum allowed ({wc.MAX_FANOUT_ITEMS})",
            )
        compiled = compile_workflow(view.spec, validate=False)
        sub = compiled["subgraphs"].get(view.node["id"])
        if sub is None:
            return _final("failed", None, error="iteration has no target node")
        target: Dict[str, Any] = {"subgraph": sub["subgraph"]}
        parallel = max(1, min(int(data.get("maxParallel", 10)), wc.MAX_PARALLEL)) if data.get("parallel") else 1
        fail_fast = data.get("errorHandling", "terminate") == "terminate"
    else:
        workflow_id = data.get("workflowId")
        if not workflow_id:
            return _final("failed", None, error="workflowId is required")
        items = [None]
        target = {"workflowId": str(workflow_id)}
        parallel, fail_fast = 1, True

    if items:
        async with tenants(str(view.tenant_id)) as db:
            rows = [
                {
                    "id": item_id(view.execution_id, view.node["id"], i),
                    "workflow_id": view.workflow_id,
                    "user_id": view.user_id,
                    "tenant_id": view.tenant_id,
                    "status": "pending",
                    "trigger": view.trigger or "manual",
                    "input_data": {
                        "__parent_run_id": str(view.execution_id),
                        "__parent_node_id": view.node["id"],
                        "__index": i,
                        "item": _json_safe(item),
                    },
                    "results": {},
                    "execution_trace": [],
                    "started_at": datetime.now(timezone.utc),
                }
                for i, item in enumerate(items)
            ]
            for start in range(0, len(rows), 200):
                await db.execute(
                    _insert_for(db).values(rows[start : start + 200]).on_conflict_do_nothing(index_elements=["id"])
                )
            await db.commit()
    body = {
        "contractVersion": wc.CONTRACT_VERSION,
        "kind": "fanout",
        "count": len(items),
        "parallel": parallel,
        "failFast": fail_fast,
        "target": target,
        "itemsRef": f"run:{view.execution_id}:{view.node['id']}",
    }
    return wc.validate_prepare_response(body).model_dump(exclude_unset=True)


def _context(view: RunView, inputs: Dict[str, Any], scope: Scope, run_id: str, db: Any) -> NodeExecutionContext:
    return NodeExecutionContext(
        node_id=view.node["id"],
        node_type=str(view.node.get("type", "")).lower(),
        node_data=view.node.get("data") or {},
        inputs=inputs,
        variables=view.variables,
        user_id=str(view.user_id),
        orchestrator=None,
        workflow_spec=view.spec,
        tenant_id=str(view.tenant_id),
        session_id=scope.conversationId,
        workflow_id=str(view.workflow_id) if view.workflow_id else None,
        workflow_run_id=run_id,
        db=db,
    )


async def run_prepare(
    tenants: TenantSessionFactory, view: RunView, scope: Scope, run_id: str, run_attempt: int
) -> Dict[str, Any]:
    node_type = str(view.node.get("type", "")).lower()
    input_class = _parse_class(scope.classification)
    if node_type in UNEXECUTED_TYPES:
        return _final(
            "skipped",
            {"message": f"Node type '{node_type}' has no executor; it is skipped."},
            classification=input_class,
        )
    executor = get_executor(node_type)
    if executor is None:
        return _final("skipped", {"message": f"No executor for node type: {node_type}"}, classification=input_class)

    inputs = predecessor_inputs(view, run_attempt)
    started = time.time()
    try:
        async with tenants(str(view.tenant_id)) as db:
            context = _context(view, inputs, scope, run_id, db)
            blocked = await executor.export_block(context, input_class)
            if blocked is not None:
                return _result_to_final(blocked, input_class)
            fanout = node_spec_for(node_type).fanout
            if fanout:
                return await _prepare_fanout(tenants, view, context, fanout)

            async def base() -> Any:
                return await executor.prepare(context, db)

            adornments = (view.node.get("data") or {}).get("adornments") or []
            cache = next((a for a in adornments if str(a.get("type", "")).lower() == "cache"), None)
            if cache is not None:
                # retry/timeout/try are the engine's (policy); the cache stays here.
                outcome = await CacheAdornment(cache).wrap_execution(executor, context, db, base)
            else:
                outcome = await base()
    except Exception as exc:  # an executor error is the node's failure, stored like any outcome
        logger.warning("node %s failed in prepare: %s", view.node["id"], exc, exc_info=True)
        return _final("failed", None, error=str(exc), classification=input_class)

    if isinstance(outcome, NodeExecutionResult):
        if not outcome.elapsed_ms:
            outcome.elapsed_ms = int((time.time() - started) * 1000)
        return _result_to_final(outcome, input_class)
    try:
        return wc.validate_prepare_response({"contractVersion": wc.CONTRACT_VERSION, **outcome}).model_dump(
            exclude_unset=True
        )
    except wc.ContractError as exc:
        return _final("failed", None, error=f"node prepare rejected: {exc.code}", classification=input_class)


async def run_finish(
    tenants: TenantSessionFactory, view: RunView, scope: Scope, run_id: str, run_attempt: int, fin: wc.FinishInput
) -> Dict[str, Any]:
    node_type = str(view.node.get("type", "")).lower()
    input_class = _parse_class(scope.classification)
    if fin.kind == "fanout":
        summary = fin.fanout
        if summary is None:
            raise _bad("fanout summary is required")
        ids = [item_id(view.execution_id, view.node["id"], i) for i in range(summary.count)]
        items: List[Dict[str, Any]] = []
        async with tenants(str(view.tenant_id)) as db:
            rows = {}
            if ids:
                found = (
                    await db.execute(
                        select(WorkflowExecution).where(
                            WorkflowExecution.tenant_id == view.tenant_id, WorkflowExecution.id.in_(ids)
                        )
                    )
                ).scalars().all()
                rows = {r.id: r for r in found}
        for i, iid in enumerate(ids):
            row = rows.get(iid)
            items.append(
                {
                    "index": i,
                    "status": row.status if row else "missing",
                    "result": (row.results or None) if row else None,
                    "error": row.error if row else "item row missing",
                }
            )
        data = view.node.get("data") or {}
        failed = summary.failed + summary.cancelled
        output = {
            "items": items,
            "count": summary.count,
            "success_count": summary.completed,
            "error_count": failed,
        }
        terminate = data.get("errorHandling", "terminate") == "terminate" if node_type == "iteration" else True
        if failed and terminate:
            error = summary.firstError if isinstance(summary.firstError, str) else json.dumps(summary.firstError, default=str)
            return _final("failed", output, error=error or "fan-out failed", classification=input_class)
        return _final("success", output, classification=input_class)

    executor = get_executor(node_type)
    if executor is None:
        raise _bad(f"node type '{node_type}' has no finish step")
    inputs = predecessor_inputs(view, run_attempt)
    try:
        async with tenants(str(view.tenant_id)) as db:
            context = _context(view, inputs, scope, run_id, db)
            result = await executor.finish(context, db, fin)
    except NotImplementedError:
        raise _bad(f"node type '{node_type}' has no finish step")
    except Exception as exc:
        logger.warning("node %s failed in finish: %s", view.node["id"], exc, exc_info=True)
        return _final("failed", None, error=str(exc), classification=input_class)
    return _result_to_final(result, input_class)


def _trace_step(view: RunView, final: Dict[str, Any], run_attempt: int, attempt: int) -> Dict[str, Any]:
    return {
        "node_id": view.node["id"],
        "node_type": str(view.node.get("type", "")).lower(),
        "run_attempt": run_attempt,
        "attempt": attempt,
        "status": final["status"],
        "output": final.get("output"),
        "error": final.get("error"),
        "tokens_used": final.get("tokensUsed", 0),
        "classification": final.get("classification"),
        "selected_handles": final.get("selectedHandles"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


# --- routes --------------------------------------------------------------------------------------


async def _perform(
    factory: SessionFactory,
    tenants: TenantSessionFactory,
    *,
    phase: str,
    key: str,
    session_id: str,
    fence_token: Optional[int],
    view: RunView,
    scope: Scope,
    run_id: str,
    run_attempt: int,
    attempt: int,
    fin: Optional[wc.FinishInput],
) -> Dict[str, Any]:
    started = False
    try:
        guard = hold_write_fence(factory, session_id, fence_token) if fence_token is not None else nullcontext()
        async with guard:
            started = True
            if phase == "prepare":
                final = await run_prepare(tenants, view, scope, run_id, run_attempt)
            else:
                assert fin is not None
                final = await run_finish(tenants, view, scope, run_id, run_attempt, fin)
            if final["kind"] == "final":
                await record_trace(tenants, view.tenant_id, view.execution_id, _trace_step(view, final, run_attempt, attempt))
            if not await invocations.store_outcome(factory, key, scope.tenantId, final):
                logger.warning("Outcome for %s was not stored: the claim is no longer in flight", key)
            return final
    except GatewayError:
        if not started:
            await invocations.release_claim(factory, key, scope.tenantId)
        raise
    except Exception:
        if not started:
            await invocations.release_claim(factory, key, scope.tenantId)
        raise


async def _handle(
    request: Request,
    run_id: str,
    node_id: str,
    phase: str,
    factory: SessionFactory,
    tenants: TenantSessionFactory,
) -> JSONResponse:
    m = parse_key(request, run_id, node_id, phase)
    key = request.headers["idempotency-key"]
    run_attempt, attempt = int(m["r"]), int(m["a"])
    run = _uuid(run_id, "runId")
    raw = await _read_body(request)
    claimed, rest = _split_scope(raw)
    fin: Optional[wc.FinishInput] = None
    if phase == "finish":
        try:
            fin = wc.validate_finish_input(rest)
        except wc.ContractError:
            raise GatewayError(422, {"error": "invalid_request"})
    else:
        extra = set(rest) - {"contractVersion", "runAttempt", "nodeAttempt"}
        if extra or rest.get("contractVersion", wc.CONTRACT_VERSION) != wc.CONTRACT_VERSION:
            raise _bad("unexpected fields in prepare body")
        # The body's attempt counters, when sent, must be the key's (the key is the call's identity).
        for field, got in (("runAttempt", m["r"]), ("nodeAttempt", m["a"])):
            if field in rest and (type(rest[field]) is not int or rest[field] != int(got)):
                raise GatewayError(
                    400, {"error": "invalid_idempotency_key", "message": f"{field} does not match the key"}
                )
    _parse_class(claimed.classification)
    scope = await bind_run(factory, run, claimed)
    tenant = _uuid(scope.tenantId, "tenantId")

    token, header_session = fence_headers(request)
    session_id = header_session or f"{scope.tenantId}/{scope.projectId}"
    if token is not None:
        await admit_fence(factory, session_id, token)

    await invocations.resolve_binding(factory, scope.tenantId, scope.projectId, scope.conversationId)
    if scope.conversationId is not None:
        async with factory() as db:
            ref = (
                await db.execute(
                    select(EngineConversation.run_kind, EngineConversation.run_ref_id).where(
                        EngineConversation.conversation_id == uuid.UUID(scope.conversationId)
                    )
                )
            ).first()
        if ref is not None and ref.run_kind == "workflow" and ref.run_ref_id != run:
            raise GatewayError(404, {"error": "conversation_not_found"})

    view = await load_run(tenants, tenant, run, node_id)
    node_type = str(view.node.get("type", "")).lower()
    if scope.nodeType is not None and scope.nodeType.lower() != node_type:
        raise _bad("nodeType does not match the workflow node")

    spec = node_spec_for(node_type)
    started = await invocations.begin(
        factory,
        key=key,
        tenant_id=scope.tenantId,
        session_id=session_id,
        tool=PHASE_TOOL[phase],
        arguments_hash=invocations.args_hash(raw),
        fence_token=token,
        timeout_ms=spec.timeout_ms,
        idempotent=spec.replay_safe,
    )
    if started.kind == "stored":
        stored = started.outcome or {}
        return _no_store(stored if "contractVersion" in stored else INTERRUPTED_FINAL)
    if started.kind == "busy":
        raise GatewayError(503, {"error": "in_flight"}, {"Retry-After": "1"})

    task = _detach(
        _perform(
            factory, tenants, phase=phase, key=key, session_id=session_id, fence_token=token, view=view,
            scope=scope, run_id=run_id, run_attempt=run_attempt, attempt=attempt, fin=fin,
        )
    )
    return _no_store(await asyncio.shield(task))


@router.post("/runs/{run_id}/nodes/{node_id}/prepare")
async def prepare_node(
    run_id: str,
    node_id: str,
    request: Request,
    factory: SessionFactory = Depends(get_session_factory),
    tenants: TenantSessionFactory = Depends(get_tenant_session_factory),
) -> JSONResponse:
    return await _handle(request, run_id, node_id, "prepare", factory, tenants)


@router.post("/runs/{run_id}/nodes/{node_id}/finish")
async def finish_node(
    run_id: str,
    node_id: str,
    request: Request,
    factory: SessionFactory = Depends(get_session_factory),
    tenants: TenantSessionFactory = Depends(get_tenant_session_factory),
) -> JSONResponse:
    return await _handle(request, run_id, node_id, "finish", factory, tenants)


@router.get("/invocations/{key}")
async def get_invocation(
    key: str, request: Request, factory: SessionFactory = Depends(get_session_factory)
) -> JSONResponse:
    """Reconcile read: the stored outcome of a completed call, else 404 ``not_found`` (also while in flight).

    Fenced like the other routes: a token raises the high-water mark even on this read, and waits for an
    older owner's in-flight write, so the 404 is final (KTD4).
    """
    token, session = fence_headers(request)
    if token is not None and session is not None:
        await admit_fence(factory, session, token)
    stored = await invocations.completed_outcome(factory, key, session)
    if stored is None:
        raise GatewayError(404, {"error": "not_found"})
    return _no_store(stored)
