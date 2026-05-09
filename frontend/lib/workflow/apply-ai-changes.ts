import type { Node, Edge } from '@xyflow/react';

/**
 * Apply AI-proposed changes (adds, modifications, deletions) to the current workflow graph.
 *
 * Pure function — unit testable without React rendering context.
 * No auto-reconnect: the AI proposes explicit edges for reconnection after deletion.
 */
export function applyAIChanges(
    currentNodes: Node[],
    currentEdges: Edge[],
    proposedNodes: Node[],
    proposedEdges: Edge[],
    operations?: { deletions?: { nodeId: string }[] }
): { nodes: Node[]; edges: Edge[] } {
    // 1. Process deletions
    const deleteNodeIds = new Set(
        operations?.deletions?.map(d => d.nodeId) || []
    );

    // 2. Remove deleted nodes and their connected edges
    const survivingNodes = currentNodes.filter(n => !deleteNodeIds.has(n.id));
    const survivingEdges = currentEdges.filter(e =>
        !deleteNodeIds.has(e.source) && !deleteNodeIds.has(e.target)
    );

    // 3. Merge modifications and additions
    const newById = new Map(proposedNodes.map(n => [n.id, n]));
    const updatedNodes = survivingNodes.map(n => newById.get(n.id) ?? n);
    const existingIds = new Set(survivingNodes.map(n => n.id));
    const addedNodes = proposedNodes.filter(n => !existingIds.has(n.id));

    // 4. Merge edge changes — remove edges targeted for deletion, add new ones
    const deleteEdgeKeys = new Set(
        proposedEdges
            .filter(e => (e.data as Record<string, unknown>)?.action === 'delete')
            .map(e => `${e.source}-${e.target}-${e.sourceHandle || ''}-${e.targetHandle || ''}`)
    );

    const keptEdges = survivingEdges.filter(e => {
        const key = `${e.source}-${e.target}-${e.sourceHandle || ''}-${e.targetHandle || ''}`;
        return !deleteEdgeKeys.has(key);
    });

    const existingEdgeIds = new Set(keptEdges.map(e => e.id));
    const addedEdges = proposedEdges.filter(e =>
        !existingEdgeIds.has(e.id) && (e.data as Record<string, unknown>)?.action !== 'delete'
    );

    return {
        nodes: [...updatedNodes, ...addedNodes],
        edges: [...keptEdges, ...addedEdges],
    };
}
