"""Workflow validation service.

Validates workflow specifications for:
- Circular dependencies (cycles in DAG)
- Complexity limits (max nodes, depth, edges)
- Structural integrity

- Phase 4: Quality Enforcement
"""
import logging
from typing import List, Optional, Set
from collections import defaultdict

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class WorkflowValidator:
    """Validates workflow specifications before save/execution."""

    @staticmethod
    def detect_cycle(nodes: List[dict], edges: List[dict]) -> Optional[List[str]]:
        """Detect cycles in workflow DAG using DFS.

        Iteration edges (sourceHandle='iteration_output') are exempted as they
        represent intentional loops.

        Args:
            nodes: List of workflow nodes
            edges: List of workflow edges

        Returns:
            List of node IDs forming the cycle, or None if acyclic

        Examples:
            >>> detect_cycle([{"id": "A"}, {"id": "B"}], [{"source": "A", "target": "B"}, {"source": "B", "target": "A"}])
            ['A', 'B', 'A']
            >>> detect_cycle([{"id": "A"}, {"id": "B"}], [{"source": "A", "target": "B"}])
            None
        """
        # Build adjacency list (skip iteration edges)
        graph = defaultdict(list)
        for edge in edges:
            # Skip iteration edges (intentional loops)
            if edge.get('sourceHandle') == 'iteration_output':
                continue
            graph[edge['source']].append(edge['target'])

        visited: Set[str] = set()
        rec_stack: List[str] = []

        def dfs(node_id: str) -> Optional[List[str]]:
            """DFS to detect cycles, returns cycle path if found."""
            if node_id in rec_stack:
                # Cycle detected - return path from cycle start to current
                cycle_start_idx = rec_stack.index(node_id)
                return rec_stack[cycle_start_idx:] + [node_id]

            if node_id in visited:
                return None

            visited.add(node_id)
            rec_stack.append(node_id)

            # Explore neighbors
            for neighbor in graph[node_id]:
                cycle = dfs(neighbor)
                if cycle:
                    return cycle

            rec_stack.pop()
            return None

        # Check from all nodes (handles disconnected components)
        for node in nodes:
            node_id = node['id']
            if node_id not in visited:
                cycle = dfs(node_id)
                if cycle:
                    return cycle

        return None

    @staticmethod
    def calculate_max_depth(nodes: List[dict], edges: List[dict]) -> int:
        """Calculate maximum depth (longest path) in workflow DAG.

        Args:
            nodes: List of workflow nodes
            edges: List of workflow edges

        Returns:
            Maximum depth (0 for empty workflow)
        """
        # Build adjacency list
        graph = {node['id']: [] for node in nodes}
        for edge in edges:
            if edge.get('sourceHandle') != 'iteration_output':
                graph[edge['source']].append(edge['target'])

        # Find start nodes (no incoming edges)
        incoming = {edge['target'] for edge in edges if edge.get('sourceHandle') != 'iteration_output'}
        start_nodes = [node['id'] for node in nodes if node['id'] not in incoming]

        if not start_nodes:
            # No clear start - might be disconnected or all nodes have inputs
            return 0

        # DFS to find longest path from each start node
        def dfs_depth(node_id: str, visited: Set[str]) -> int:
            if not graph[node_id]:
                # Leaf node
                return 0

            if node_id in visited:
                # Cycle (shouldn't happen if validation called first)
                return 0

            visited_copy = visited.copy()
            visited_copy.add(node_id)

            # Max depth of any child + 1
            child_depths = [dfs_depth(child, visited_copy) for child in graph[node_id]]
            return max(child_depths, default=0) + 1

        max_depth = 0
        for start in start_nodes:
            depth = dfs_depth(start, set())
            max_depth = max(max_depth, depth)

        return max_depth

    @staticmethod
    def validate_workflow(
        spec: dict,
        max_nodes: int = None,
        max_depth: int = None,
        max_edges: int = None
    ) -> None:
        """Validate complete workflow specification.

        Args:
            spec: Workflow specification dict with 'nodes' and 'edges'
            max_nodes: Maximum allowed nodes (uses config default if None)
            max_depth: Maximum allowed depth (uses config default if None)
            max_edges: Maximum allowed edges (uses config default if None)

        Raises:
            ValueError: If validation fails with descriptive error message
        """
        # Use config defaults if not specified
        if max_nodes is None:
            max_nodes = settings.MAX_WORKFLOW_NODES
        if max_depth is None:
            max_depth = settings.MAX_WORKFLOW_DEPTH
        if max_edges is None:
            max_edges = settings.MAX_WORKFLOW_EDGES

        nodes = spec.get('nodes', [])
        edges = spec.get('edges', [])

        # Check node count
        if len(nodes) > max_nodes:
            raise ValueError(
                f"Workflow exceeds maximum node count: {len(nodes)} > {max_nodes}. "
                f"Consider splitting into sub-workflows."
            )

        # Check edge count
        if len(edges) > max_edges:
            raise ValueError(
                f"Workflow exceeds maximum edge count: {len(edges)} > {max_edges}"
            )

        # Check for cycles
        cycle = WorkflowValidator.detect_cycle(nodes, edges)
        if cycle:
            # Get node labels for better error message
            node_map = {n['id']: n for n in nodes}
            cycle_labels = []
            for node_id in cycle:
                node = node_map.get(node_id)
                if node:
                    label = node.get('data', {}).get('label') or node.get('data', {}).get('name') or node_id[:8]
                    cycle_labels.append(label)
                else:
                    cycle_labels.append(node_id[:8])

            raise ValueError(
                f"Workflow contains circular dependency: {' → '.join(cycle_labels)}. "
                f"Remove the loop or use an Iteration node for intentional loops."
            )

        # Check depth
        depth = WorkflowValidator.calculate_max_depth(nodes, edges)
        if depth > max_depth:
            raise ValueError(
                f"Workflow exceeds maximum depth: {depth} > {max_depth}. "
                f"Consider parallelizing sequential nodes."
            )

        # Validate node types against executor registry
        WorkflowValidator.validate_node_types(nodes)

        logger.debug(f"Workflow validation passed: {len(nodes)} nodes, {len(edges)} edges, depth {depth}")

    @staticmethod
    def validate_node_types(nodes: List[dict]) -> None:
        """Validate that all node types have registered executors.

        Passthrough types (start, end, input, output, variable_aggregator)
        are always allowed as they are handled by the orchestrator.

        Args:
            nodes: List of workflow nodes

        Raises:
            ValueError: If any node has an unrecognized type
        """
        from app.node_executors import get_all_executors

        passthrough_types = {"start", "end", "input", "output", "variable_aggregator"}
        known_types = set(get_all_executors().keys()) | passthrough_types

        unknown = []
        for node in nodes:
            node_type = node.get("type", "").lower()
            if node_type and node_type not in known_types:
                label = node.get("data", {}).get("label") or node.get("data", {}).get("name") or node["id"][:8]
                unknown.append(f"{label} ({node_type})")

        if unknown:
            available = sorted(known_types)
            raise ValueError(
                f"Unrecognized node types: {', '.join(unknown)}. "
                f"Available types: {', '.join(available)}"
            )
