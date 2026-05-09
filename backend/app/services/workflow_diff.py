"""Workflow version diff computation.

Computes differences between two workflow specifications for version comparison.

Identifies:
- Nodes added, removed, modified
- Edges added, removed
- Field-level changes within nodes

- Phase 4: Quality & Versioning
"""
import logging
from typing import Dict, List, Any
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class NodeChange(BaseModel):
    """Represents changes to a single node."""
    node_id: str
    node_label: str
    node_type: str
    field_changes: Dict[str, Dict[str, Any]]  # field_name -> {old, new}


class WorkflowDiff(BaseModel):
    """Complete diff between two workflow versions."""
    nodes_added: List[Dict[str, Any]]
    nodes_removed: List[Dict[str, Any]]
    nodes_modified: List[NodeChange]
    edges_added: List[Dict[str, Any]]
    edges_removed: List[Dict[str, Any]]
    summary: str


def compute_workflow_diff(spec_a: dict, spec_b: dict) -> WorkflowDiff:
    """Compute diff between two workflow specifications.

    Args:
        spec_a: Base version workflow spec
        spec_b: Compare version workflow spec

    Returns:
        WorkflowDiff with added/removed/modified elements
    """
    nodes_a = {n['id']: n for n in spec_a.get('nodes', [])}
    nodes_b = {n['id']: n for n in spec_b.get('nodes', [])}

    # Compute node sets
    added_ids = set(nodes_b.keys()) - set(nodes_a.keys())
    removed_ids = set(nodes_a.keys()) - set(nodes_b.keys())
    common_ids = set(nodes_a.keys()) & set(nodes_b.keys())

    nodes_added = [nodes_b[nid] for nid in added_ids]
    nodes_removed = [nodes_a[nid] for nid in removed_ids]

    # Check for modifications in common nodes
    nodes_modified = []
    for node_id in common_ids:
        node_a = nodes_a[node_id]
        node_b = nodes_b[node_id]

        if node_a != node_b:
            # Node was modified - compute field-level diff
            field_changes = _deep_diff(node_a, node_b)
            if field_changes:
                nodes_modified.append(NodeChange(
                    node_id=node_id,
                    node_label=node_a.get('data', {}).get('label', node_id[:8]),
                    node_type=node_a.get('type', 'unknown'),
                    field_changes=field_changes
                ))

    # Compute edge sets
    edges_a = {_edge_key(e): e for e in spec_a.get('edges', [])}
    edges_b = {_edge_key(e): e for e in spec_b.get('edges', [])}

    added_edge_keys = set(edges_b.keys()) - set(edges_a.keys())
    removed_edge_keys = set(edges_a.keys()) - set(edges_b.keys())

    edges_added = [edges_b[k] for k in added_edge_keys]
    edges_removed = [edges_a[k] for k in removed_edge_keys]

    # Generate summary
    summary = _generate_summary(
        len(nodes_added), len(nodes_removed), len(nodes_modified),
        len(edges_added), len(edges_removed)
    )

    return WorkflowDiff(
        nodes_added=nodes_added,
        nodes_removed=nodes_removed,
        nodes_modified=nodes_modified,
        edges_added=edges_added,
        edges_removed=edges_removed,
        summary=summary
    )


def _edge_key(edge: dict) -> str:
    """Generate unique key for edge comparison."""
    source = edge.get('source', '')
    target = edge.get('target', '')
    source_handle = edge.get('sourceHandle', '')
    target_handle = edge.get('targetHandle', '')
    return f"{source}:{source_handle}→{target}:{target_handle}"


def _deep_diff(obj_a: Any, obj_b: Any, path: str = "") -> Dict[str, Dict[str, Any]]:
    """Recursively compute diff between two objects.

    Args:
        obj_a: Original object
        obj_b: New object
        path: Current path in object tree (for nested diffs)

    Returns:
        Dict of field_path -> {old: value_a, new: value_b}
    """
    changes = {}

    if type(obj_a) != type(obj_b):
        changes[path or "root"] = {"old": obj_a, "new": obj_b}
        return changes

    if isinstance(obj_a, dict) and isinstance(obj_b, dict):
        # Compare dict keys
        all_keys = set(obj_a.keys()) | set(obj_b.keys())
        for key in all_keys:
            key_path = f"{path}.{key}" if path else key

            if key not in obj_a:
                changes[key_path] = {"old": None, "new": obj_b[key]}
            elif key not in obj_b:
                changes[key_path] = {"old": obj_a[key], "new": None}
            elif obj_a[key] != obj_b[key]:
                # Recursively diff nested objects
                if isinstance(obj_a[key], (dict, list)):
                    nested_changes = _deep_diff(obj_a[key], obj_b[key], key_path)
                    changes.update(nested_changes)
                else:
                    changes[key_path] = {"old": obj_a[key], "new": obj_b[key]}

    elif isinstance(obj_a, list) and isinstance(obj_b, list):
        if obj_a != obj_b:
            changes[path or "root"] = {"old": obj_a, "new": obj_b}

    elif obj_a != obj_b:
        changes[path or "root"] = {"old": obj_a, "new": obj_b}

    return changes


def _generate_summary(
    nodes_added: int,
    nodes_removed: int,
    nodes_modified: int,
    edges_added: int,
    edges_removed: int
) -> str:
    """Generate human-readable diff summary."""
    parts = []

    if nodes_added:
        parts.append(f"{nodes_added} node{'s' if nodes_added != 1 else ''} added")
    if nodes_removed:
        parts.append(f"{nodes_removed} node{'s' if nodes_removed != 1 else ''} removed")
    if nodes_modified:
        parts.append(f"{nodes_modified} node{'s' if nodes_modified != 1 else ''} modified")
    if edges_added:
        parts.append(f"{edges_added} edge{'s' if edges_added != 1 else ''} added")
    if edges_removed:
        parts.append(f"{edges_removed} edge{'s' if edges_removed != 1 else ''} removed")

    if not parts:
        return "No changes"

    return ", ".join(parts)
