"""Workflow spec compiler (plan 2026-10-17-003, U13, KTD5).

Turns a React Flow spec into the compiled form the engine schedules from:
``{ nodes: [{ id, type, deps: [{ from, handle }], policy, ... }], errorMode, warnings }``.
Graph semantics stay here; the engine only needs ready-sets and dead edges.
"""
from __future__ import annotations

import copy
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.workflow_validator import WorkflowValidator

#: Builder node types with no executor: compiled as pass-through ``skipped`` (today's silent skip).
UNEXECUTED_TYPES = frozenset({"data", "analysis", "research", "schedule_trigger"})
#: Types that choose among outgoing handles.
BRANCHING_TYPES = frozenset({"ifelse", "classifier", "conditional"})
ITERATION_EDGE = "iteration_output"


def _node_type(node: Dict[str, Any]) -> str:
    return str(node.get("type", "")).lower()


def _policy(node: Dict[str, Any], fanout: bool) -> Dict[str, Any]:
    """Adornments the engine owns: retry -> maxRetries/backoffMs, timeout -> timeoutMs, try -> onError."""
    policy: Dict[str, Any] = {"maxRetries": 0}
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
        policy.pop("backoffMs", None)
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

    # An iteration's target and everything reachable from it run as that node's sub-graph.
    subgraphs: Dict[str, Dict[str, Any]] = {}
    excluded: Set[str] = set()
    for node in nodes:
        data = node.get("data") or {}
        target = data.get("targetNodeId")
        if _node_type(node) == "iteration" and target in by_id:
            members = _reachable(target, edges) - {node["id"]}
            excluded |= members
            subgraphs[node["id"]] = {
                "subgraph": f"{node['id']}:sub",
                "entry": target,
                "nodes": sorted(members),
            }

    handles: Dict[str, List[str]] = defaultdict(list)
    for edge in edges:
        handle = edge.get("sourceHandle")
        if handle and handle != ITERATION_EDGE and handle not in handles[edge["source"]]:
            handles[edge["source"]].append(handle)

    warnings: List[Dict[str, str]] = []
    compiled_nodes: List[Dict[str, Any]] = []
    error_mode = "stop"
    for node in nodes:
        node_id = node["id"]
        if node_id in excluded:
            continue
        node_type = _node_type(node)
        deps = [
            {"from": e["source"], "handle": e.get("sourceHandle") or None}
            for e in edges
            if e["target"] == node_id
            and e["source"] not in excluded
            and e.get("sourceHandle") != ITERATION_EDGE
        ]
        fanout_node = node_id in subgraphs or node_type == "subworkflow"
        entry: Dict[str, Any] = {
            "id": node_id,
            "type": node_type,
            "deps": deps,
            "policy": _policy(node, fanout_node),
        }
        if entry["policy"].get("onError") == "continue":
            error_mode = "continue"
        if node_type in BRANCHING_TYPES:
            entry["handles"] = list(handles.get(node_id, []))
        if node_id in subgraphs:
            entry["fanout"] = dict(subgraphs[node_id])
        elif node_type == "subworkflow":
            entry["fanout"] = {"workflowId": (node.get("data") or {}).get("workflowId")}
        if node_type in UNEXECUTED_TYPES:
            entry["skip"] = True
            message = f"Node type '{node_type}' has no executor; it is skipped."
            entry["warning"] = message
            warnings.append({"nodeId": node_id, "message": message})
        compiled_nodes.append(entry)

    return {
        "nodes": compiled_nodes,
        "errorMode": error_mode,
        "subgraphs": {k: v for k, v in subgraphs.items()},
        "warnings": warnings,
    }


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
