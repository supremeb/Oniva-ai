"""
Workflow Tool Generator — produces Pydantic AI tool functions from workflow specs.

Each workflow attached to an agent becomes a callable tool that returns a
**step preview** (not execution).  The preview includes every node the
workflow will traverse, flags human-in-the-loop steps, and summarises the
inputs the user provided.  Actual execution happens in Phase 2 after the
user approves the preview.

Usage::

    from app.agents.skills.workflow_tool_generator import generate_workflow_tools

    tools = generate_workflow_tools(workflows)   # list[Tool]
    # Pass to agent resolver / wire into FunctionToolset
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any

from pydantic_ai.tools import Tool

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Type mapping: workflow variable type → Python annotation
# -----------------------------------------------------------------------
_TYPE_MAP: dict[str, type] = {
    "string": str,
    "number": float,
    "boolean": bool,
    "array": list,
}


def _python_type(var_type: str) -> type:
    """Map a workflow variable type string to a Python type."""
    return _TYPE_MAP.get(var_type, str)


# -----------------------------------------------------------------------
# Name sanitization
# -----------------------------------------------------------------------
_SANITIZE_RE = re.compile(r"[^a-z0-9]+")


def _sanitize_name(name: str) -> str:
    """Convert a human-readable workflow name to a valid Python identifier.

    Example: "KYC Screening Pipeline" → "run_kyc_screening_pipeline"
    """
    slug = _SANITIZE_RE.sub("_", name.lower()).strip("_")
    return f"run_{slug}"


# -----------------------------------------------------------------------
# Edge-order traversal
# -----------------------------------------------------------------------

def _traverse_nodes_by_edges(spec: dict) -> list[dict]:
    """Walk the graph from the start node following edges to produce an
    ordered list of non-start nodes.

    For branching graphs (if/else) we do a BFS so both branches appear.
    """
    nodes_by_id: dict[str, dict] = {n["id"]: n for n in spec.get("nodes", [])}
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in spec.get("edges", []):
        adjacency[edge["source"]].append(edge["target"])

    # Find start node
    start_id: str | None = None
    for node in spec.get("nodes", []):
        if node.get("type") == "start":
            start_id = node["id"]
            break

    if start_id is None:
        # Fallback: return all non-start nodes in spec order
        return [n for n in spec.get("nodes", []) if n.get("type") != "start"]

    # BFS from start
    ordered: list[dict] = []
    visited: set[str] = {start_id}
    queue: list[str] = list(adjacency.get(start_id, []))

    while queue:
        nid = queue.pop(0)
        if nid in visited:
            continue
        visited.add(nid)
        node = nodes_by_id.get(nid)
        if node:
            ordered.append(node)
        for child in adjacency.get(nid, []):
            if child not in visited:
                queue.append(child)

    return ordered


# -----------------------------------------------------------------------
# Core: extract start variables
# -----------------------------------------------------------------------

def _extract_start_variables(spec: dict) -> list[dict[str, str]]:
    """Return the variable definitions from the start node.

    Each entry has ``{"name": ..., "type": ...}``.
    """
    for node in spec.get("nodes", []):
        if node.get("type") == "start":
            return node.get("data", {}).get("variables", [])
    return []


# -----------------------------------------------------------------------
# HITL detection
# -----------------------------------------------------------------------

def _detect_hitl(nodes: list[dict]) -> tuple[bool, str | None]:
    """Return (has_hitl, warning_message | None)."""
    hitl_labels: list[str] = []
    for node in nodes:
        if node.get("type") == "human_in_loop":
            label = node.get("data", {}).get("label", node["id"])
            hitl_labels.append(label)

    if not hitl_labels:
        return False, None

    parts = ", ".join(f'"{l}"' for l in hitl_labels)
    warning = (
        f"This workflow contains {len(hitl_labels)} human-in-the-loop "
        f"step(s) requiring manual approval: {parts}. "
        f"Execution will pause at these steps until a human responds."
    )
    return True, warning


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------

def generate_workflow_tools(workflows: list) -> list[Tool]:
    """Generate Pydantic AI-compatible ``Tool`` objects for workflows.

    Each workflow becomes a tool that returns a step preview (not execution).
    The preview is a dict the LLM can relay to the user for approval before
    the actual workflow runs.

    Args:
        workflows: List of Workflow ORM objects (or any object with
            ``.id``, ``.name``, ``.description``, and ``.spec`` attrs).

    Returns:
        List of ``pydantic_ai.tools.Tool`` instances ready to be added to
        a ``FunctionToolset``.
    """
    tools: list[Tool] = []
    used_names: dict[str, int] = {}

    for workflow in workflows:
        spec = workflow.spec or {}
        if not spec.get("nodes"):
            logger.warning(
                "Workflow %s (%s) has no nodes — skipping tool generation",
                workflow.name, workflow.id,
            )
            continue

        # --- Sanitize name, handle duplicates ---
        base_name = _sanitize_name(workflow.name)
        if base_name in used_names:
            used_names[base_name] += 1
            func_name = f"{base_name}_{used_names[base_name]}"
        else:
            used_names[base_name] = 1
            func_name = base_name

        # --- Extract start variables (tool parameters) ---
        start_vars = _extract_start_variables(spec)

        # --- Ordered non-start nodes for step preview ---
        ordered_nodes = _traverse_nodes_by_edges(spec)
        steps = [
            {
                "node_id": n["id"],
                "label": n.get("data", {}).get("label", n["id"]),
                "type": n.get("type", "unknown"),
            }
            for n in ordered_nodes
        ]

        # --- HITL detection ---
        has_hitl, hitl_warning = _detect_hitl(ordered_nodes)

        # --- Build the closure ---
        # Capture loop variables via default args
        wf_id = str(workflow.id)
        wf_name = workflow.name
        raw_desc = workflow.description or f"Run the '{workflow.name}' workflow."
        wf_description = f"[Workflow] {raw_desc}"

        if start_vars:
            # Build a closure that accepts the declared variables as **kwargs
            def _make_tool(
                _wf_id: str,
                _wf_name: str,
                _steps: list[dict],
                _has_hitl: bool,
                _hitl_warning: str | None,
                _var_names: list[str],
            ):
                async def tool_fn(**kwargs: Any) -> dict[str, Any]:
                    input_summary = {k: kwargs.get(k) for k in _var_names}
                    return {
                        "status": "pending_approval",
                        "workflow_id": _wf_id,
                        "workflow_name": _wf_name,
                        "steps": _steps,
                        "has_hitl_nodes": _has_hitl,
                        "hitl_warning": _hitl_warning,
                        "input_summary": input_summary,
                    }

                # Build parameter annotations dynamically
                annotations: dict[str, type] = {}
                for v in _var_names:
                    annotations[v] = str  # will be overridden below
                tool_fn.__annotations__ = annotations
                return tool_fn

            var_names = [v["name"] for v in start_vars]
            closure = _make_tool(
                wf_id, wf_name, steps, has_hitl, hitl_warning, var_names,
            )
            # Apply correct type annotations from variable definitions
            closure.__annotations__ = {
                v["name"]: _python_type(v.get("type", "string"))
                for v in start_vars
            }
        else:
            # No declared variables — provide a single `query: str` fallback
            def _make_fallback_tool(
                _wf_id: str,
                _wf_name: str,
                _steps: list[dict],
                _has_hitl: bool,
                _hitl_warning: str | None,
            ):
                async def tool_fn(query: str) -> dict[str, Any]:
                    return {
                        "status": "pending_approval",
                        "workflow_id": _wf_id,
                        "workflow_name": _wf_name,
                        "steps": _steps,
                        "has_hitl_nodes": _has_hitl,
                        "hitl_warning": _hitl_warning,
                        "input_summary": {"query": query},
                    }
                return tool_fn

            closure = _make_fallback_tool(
                wf_id, wf_name, steps, has_hitl, hitl_warning,
            )

        closure.__name__ = func_name
        closure.__qualname__ = func_name
        closure.__doc__ = wf_description

        # --- Wrap in Pydantic AI Tool ---
        tool = Tool(
            closure,
            takes_ctx=False,
            name=func_name,
            description=wf_description,
        )
        tools.append(tool)

    return tools
