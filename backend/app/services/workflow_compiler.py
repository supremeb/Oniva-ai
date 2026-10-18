"""Workflow spec compiler (plan 2026-10-17-003, U13/U15, KTD5).

Turns a React Flow spec into the graph the engine schedules from. ``compile_workflow`` returns::

    { nodes: [{ id, type, deps: [{ from, handle }], policy, handles?, mayFanOut? }],
      subgraphs: { "<iteration>:sub": { nodes, subgraphs? } },
      workflows: { "<workflowId>": { nodes, ... } },   # filled by ``compile_for_run``
      errorMode, warnings }

``engine_graph`` keeps only the keys ``engine/src/workflow/graph.ts`` accepts (its schema is strict:
a stray key, a partial ``policy`` or a handle the source never declares is refused with a 400, and
a fan-out node without ``mayFanOut`` hangs the run). ``build_run_input`` wraps it as the
``workflow_run`` task input. Graph semantics stay here; the engine only needs ready-sets and dead edges.
"""
from __future__ import annotations

import copy
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.internal_api.invocations import STALE_GRACE_S
from app.internal_api.node_specs import node_spec_for
from app.services.workflow_validator import WorkflowValidator

#: Builder node types with no executor: compiled as pass-through ``skipped`` (today's silent skip).
UNEXECUTED_TYPES = frozenset({"data", "analysis", "research", "schedule_trigger"})
#: Types that choose among outgoing handles.
BRANCHING_TYPES = frozenset({"ifelse", "classifier", "conditional"})
ITERATION_EDGE = "iteration_output"
#: Mirrors ``MAX_WORKFLOW_NODES`` / ``MAX_SUBWORKFLOW_DEPTH`` in ``engine/src/workflow/graph.ts``.
MAX_ENGINE_NODES = 95
MAX_SUBWORKFLOW_DEPTH = 3
#: The graph keys the engine's strict schema allows per node and per graph.
ENGINE_NODE_KEYS = ("id", "type", "deps", "policy", "handles", "mayFanOut")


def _node_type(node: Dict[str, Any]) -> str:
    return str(node.get("type", "")).lower()


def subgraph_name(node_id: str) -> str:
    """The name an iteration's body goes by in ``subgraphs`` and in its fan-out target."""
    return f"{node_id}:sub"


def _policy(node: Dict[str, Any], fanout: bool) -> Dict[str, Any]:
    """The engine's four-field policy. Adornments: retry -> maxRetries/backoffMs, timeout -> timeoutMs,
    try -> onError. The timeout defaults to the node type's gateway timeout plus twice the stale-claim grace, so the
    engine outlasts a claim left ``in_flight`` by a dead backend and sees it resolve (``interrupted``) instead
    of timing out and retrying an effect that may have happened."""
    policy: Dict[str, Any] = {
        "maxRetries": 0,
        "backoffMs": 0,
        "timeoutMs": node_spec_for(_node_type(node)).timeout_ms + 2 * STALE_GRACE_S * 1000,
        "onError": "stop",
    }
    for adornment in (node.get("data") or {}).get("adornments") or []:
        kind = str(adornment.get("type", "")).lower()
        if kind == "retry":
            policy["maxRetries"] = int(adornment.get("maxRetries", 3))
            policy["backoffMs"] = int(float(adornment.get("backoffSeconds", 1)) * 1000)
        elif kind == "timeout":
            policy["timeoutMs"] = int(float(adornment.get("timeoutSeconds", 60)) * 1000)
        elif kind == "try":
            policy["onError"] = "continue"
    if fanout:
        policy["maxRetries"] = 0
        policy["backoffMs"] = 0
    return policy


def _reachable(start: str, edges: List[Dict[str, Any]]) -> Set[str]:
    out: Dict[str, List[str]] = defaultdict(list)
    for edge in edges:
        if edge.get("sourceHandle") != ITERATION_EDGE:
            out[edge["source"]].append(edge["target"])
    seen: Set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(out.get(current, []))
    return seen


def _build_graph(
    scope: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    by_id: Dict[str, Dict[str, Any]],
    warnings: List[Dict[str, str]],
    flags: Dict[str, bool],
) -> Dict[str, Any]:
    """One graph level: ``scope`` nodes in spec order, with each iteration body split into a subgraph."""
    in_scope = {n["id"] for n in scope}
    iterations: Dict[str, Set[str]] = {}
    for node in scope:
        target = (node.get("data") or {}).get("targetNodeId")
        if _node_type(node) == "iteration" and target in in_scope:
            iterations[node["id"]] = (_reachable(target, edges) & in_scope) - {node["id"]}
    # An iteration inside another's body belongs to that body.
    nested = {i for i in iterations for other, body in iterations.items() if other != i and i in body}
    top = {i: body for i, body in iterations.items() if i not in nested}
    excluded: Set[str] = set().union(*top.values()) if top else set()

    subgraphs: Dict[str, Any] = {}
    for it_id, body in top.items():
        members = [n for n in scope if n["id"] in body]
        subgraphs[subgraph_name(it_id)] = _build_graph(members, edges, by_id, warnings, flags)

    # A node's edge handle counts only when its source can select one (the engine rejects any other).
    handles: Dict[str, List[str]] = defaultdict(list)
    for edge in edges:
        handle = edge.get("sourceHandle")
        source = by_id.get(edge["source"])
        if handle and handle != ITERATION_EDGE and source is not None and _node_type(source) in BRANCHING_TYPES:
            if handle not in handles[edge["source"]]:
                handles[edge["source"]].append(handle)

    nodes: List[Dict[str, Any]] = []
    for node in scope:
        node_id = node["id"]
        if node_id in excluded:
            continue
        node_type = _node_type(node)
        deps = []
        for e in edges:
            if e["target"] != node_id or e["source"] not in in_scope or e["source"] in excluded:
                continue
            if e.get("sourceHandle") == ITERATION_EDGE:
                continue
            source_type = _node_type(by_id[e["source"]])
            handle = e.get("sourceHandle") if source_type in BRANCHING_TYPES else None
            deps.append({"from": e["source"], "handle": handle or None})
        fanout = node_spec_for(node_type).fanout is not None
        entry: Dict[str, Any] = {
            "id": node_id,
            "type": node_type,
            "deps": deps,
            "policy": _policy(node, fanout),
        }
        if entry["policy"]["onError"] == "continue":
            flags["continue"] = True
        if node_type in BRANCHING_TYPES:
            entry["handles"] = list(handles.get(node_id, []))
        if fanout:
            entry["mayFanOut"] = True
        if node_type in UNEXECUTED_TYPES:
            warnings.append({"nodeId": node_id, "message": f"Node type '{node_type}' has no executor; it is skipped."})
        nodes.append(entry)

    graph: Dict[str, Any] = {"nodes": nodes}
    if subgraphs:
        graph["subgraphs"] = subgraphs
    return graph


def compile_workflow(spec: Dict[str, Any], *, validate: bool = True) -> Dict[str, Any]:
    """Compile ``spec``. Raises ``ValueError`` (the validator's message) for an invalid or unsupported spec."""
    nodes: List[Dict[str, Any]] = list(spec.get("nodes") or [])
    edges: List[Dict[str, Any]] = list(spec.get("edges") or [])

    if validate:
        checked = copy.copy(spec)
        # The four unexecuted builder types are allowed; everything else must have an executor.
        checked["nodes"] = [
            {**n, "type": "end"} if _node_type(n) in UNEXECUTED_TYPES else n for n in nodes
        ]
        WorkflowValidator.validate_workflow(checked)

    by_id = {n["id"]: n for n in nodes}
    warnings: List[Dict[str, str]] = []
    flags = {"continue": False}
    graph = _build_graph(nodes, edges, by_id, warnings, flags)
    return {
        "nodes": graph["nodes"],
        "subgraphs": graph.get("subgraphs", {}),
        "workflows": {},
        "errorMode": "continue" if flags["continue"] else "stop",
        "warnings": warnings,
    }


def _strict(graph: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"nodes": [{k: n[k] for k in ENGINE_NODE_KEYS if k in n} for n in graph["nodes"]]}
    if graph.get("subgraphs"):
        out["subgraphs"] = {name: _strict(g) for name, g in graph["subgraphs"].items()}
    if graph.get("workflows"):
        out["workflows"] = {wid: _strict(g) for wid, g in graph["workflows"].items()}
    return out


def engine_graph(compiled: Dict[str, Any]) -> Dict[str, Any]:
    """The compiled graph in exactly the shape the engine's strict schema accepts."""
    return _strict(compiled)


def count_nodes(graph: Dict[str, Any]) -> int:
    total = len(graph.get("nodes") or [])
    for key in ("subgraphs", "workflows"):
        total += sum(count_nodes(g) for g in (graph.get(key) or {}).values())
    return total


def _level_subworkflow_ids(graph: Dict[str, Any], by_id: Dict[str, Dict[str, Any]]) -> List[str]:
    ids: List[str] = []
    for entry in graph["nodes"]:
        if entry["type"] == "subworkflow":
            wid = ((by_id.get(entry["id"]) or {}).get("data") or {}).get("workflowId")
            if wid and str(wid) not in ids:
                ids.append(str(wid))
    return ids


async def _attach_workflows(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    graph: Dict[str, Any],
    spec: Dict[str, Any],
    ancestors: List[str],
    depth: int,
) -> None:
    """Resolve the workflows each graph level references into that level's ``workflows``.

    The engine resolves a ``{workflowId}`` target against the graph the node runs in, so a subworkflow
    inside an iteration body is attached to that body's subgraph, not to the root.
    """
    by_id = {n["id"]: n for n in spec.get("nodes") or []}
    for wid in _level_subworkflow_ids(graph, by_id):
        if wid in ancestors:
            raise ValueError(f"workflow {wid} references itself")
        if depth + 1 > MAX_SUBWORKFLOW_DEPTH:
            raise ValueError(f"subworkflows nest at most {MAX_SUBWORKFLOW_DEPTH} levels deep")
        try:
            wf_uuid = uuid.UUID(wid)
        except ValueError:
            raise ValueError(f"subworkflow id {wid!r} is not a workflow id")
        child_spec = await resolve_spec(db, tenant_id, wf_uuid)
        if child_spec is None:
            raise ValueError(f"subworkflow {wid} was not found")
        child = compile_workflow(child_spec)
        child_graph = {k: child[k] for k in ("nodes", "subgraphs", "workflows")}
        await _attach_workflows(db, tenant_id, child_graph, child_spec, [*ancestors, wid], depth + 1)
        graph.setdefault("workflows", {})[wid] = child_graph
    for sub in (graph.get("subgraphs") or {}).values():
        await _attach_workflows(db, tenant_id, sub, spec, ancestors, depth)


async def compile_for_run(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    spec: Dict[str, Any],
    *,
    workflow_id: Optional[uuid.UUID] = None,
) -> Dict[str, Any]:
    """``compile_workflow`` plus the referenced subworkflows, ready for ``build_run_input``.

    Subworkflows inside iteration bodies are found too. Raises ``ValueError`` for an invalid spec, a
    missing or self-referencing subworkflow, nesting past the engine's cap or more nodes than it allows.
    """
    compiled = compile_workflow(spec)
    ancestors = [str(workflow_id)] if workflow_id is not None else []
    await _attach_workflows(db, tenant_id, compiled, spec, ancestors, 0)
    total = count_nodes(compiled)
    if total > MAX_ENGINE_NODES:
        raise ValueError(f"Workflow exceeds maximum node count ({MAX_ENGINE_NODES}) across subgraphs and subworkflows, got {total}")
    return compiled


def build_run_input(
    run_id: Any,
    compiled: Dict[str, Any],
    *,
    run_attempt: int = 0,
    retry: Optional[Dict[str, int]] = None,
    deadline_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """The ``workflow_run`` task input (``parseRunInput`` in ``engine/src/workflow/run-task.ts``).

    ``variables`` stay with the backend (the run's execution row); the engine never carries payloads.
    """
    body: Dict[str, Any] = {
        "version": 1,
        "runId": str(run_id),
        "runAttempt": run_attempt,
        "graph": engine_graph(compiled),
        "variables": {},
        "errorMode": compiled["errorMode"],
    }
    if retry is not None:
        body["retry"] = {"max": int(retry["max"]), "delayMs": int(retry["delayMs"])}
    if deadline_ms is not None:
        body["deadlineMs"] = int(deadline_ms)
    return body


def find_entry(compiled: Dict[str, Any], node_id: str) -> Optional[Dict[str, Any]]:
    """The compiled entry of ``node_id`` in the root graph or any iteration body."""
    graphs = [compiled]
    while graphs:
        graph = graphs.pop()
        for entry in graph["nodes"]:
            if entry["id"] == node_id:
                return entry
        graphs.extend((graph.get("subgraphs") or {}).values())
    return None


def untaken_edges(compiled: Dict[str, Any], node_id: str, selected_handles: Optional[List[str]]) -> List[Dict[str, Any]]:
    """Edges out of ``node_id`` whose handle was not selected (dead edges the scheduler skips).

    Edges without a handle are always live. ``selected_handles`` of None means "no branching decision".
    """
    if selected_handles is None:
        return []
    chosen = set(selected_handles)
    dead = []
    for node in compiled["nodes"]:
        for dep in node["deps"]:
            if dep["from"] == node_id and dep["handle"] and dep["handle"] not in chosen:
                dead.append({"from": node_id, "to": node["id"], "handle": dep["handle"]})
    return dead


async def resolve_spec(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID,
    workflow_version_id: Optional[uuid.UUID] = None,
) -> Optional[Dict[str, Any]]:
    """The spec a run executes: a pinned version's frozen spec, else the workflow's current one."""
    from app.models.workflow import Workflow, WorkflowVersion

    if workflow_version_id is not None:
        found = (
            await db.execute(
                select(WorkflowVersion.spec).where(
                    WorkflowVersion.id == workflow_version_id,
                    WorkflowVersion.workflow_id == workflow_id,
                    WorkflowVersion.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        return found
    return (
        await db.execute(select(Workflow.spec).where(Workflow.id == workflow_id, Workflow.tenant_id == tenant_id))
    ).scalar_one_or_none()
