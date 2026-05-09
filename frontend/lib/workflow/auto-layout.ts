import Dagre from '@dagrejs/dagre';
import type { Node, Edge } from '@xyflow/react';

export interface LayoutOptions {
    direction?: 'LR' | 'TB' | 'RL' | 'BT'; // Left-Right, Top-Bottom, etc.
    nodeWidth?: number;
    nodeHeight?: number;
    horizontalSpacing?: number;
    verticalSpacing?: number;
}

const DEFAULT_OPTIONS: Required<LayoutOptions> = {
    direction: 'LR', // Left to right by default
    nodeWidth: 280,
    nodeHeight: 120,
    horizontalSpacing: 100,
    verticalSpacing: 80,
};

/**
 * Auto-layout nodes using dagre algorithm
 * Creates a clean left-to-right (or configurable direction) layout
 */
export function autoLayoutNodes(
    nodes: Node[],
    edges: Edge[],
    options: LayoutOptions = {}
): Node[] {
    if (nodes.length === 0) return nodes;

    const opts = { ...DEFAULT_OPTIONS, ...options };

    // Create a new dagre graph
    const g = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));

    // Set graph direction and spacing
    g.setGraph({
        rankdir: opts.direction,
        nodesep: opts.verticalSpacing,
        ranksep: opts.horizontalSpacing,
        marginx: 50,
        marginy: 50,
    });

    // Add nodes to the graph
    nodes.forEach((node) => {
        g.setNode(node.id, {
            width: opts.nodeWidth,
            height: opts.nodeHeight,
        });
    });

    // Add edges to the graph
    edges.forEach((edge) => {
        g.setEdge(edge.source, edge.target);
    });

    // Run the dagre layout algorithm
    Dagre.layout(g);

    // Map the dagre positions back to React Flow nodes
    return nodes.map((node) => {
        const nodeWithPosition = g.node(node.id);

        if (!nodeWithPosition) {
            return node;
        }

        return {
            ...node,
            position: {
                // Dagre gives center positions, we need top-left for React Flow
                x: nodeWithPosition.x - opts.nodeWidth / 2,
                y: nodeWithPosition.y - opts.nodeHeight / 2,
            },
        };
    });
}

/**
 * Get layout direction based on the current flow structure
 * Analyzes existing node positions to suggest best direction
 */
export function suggestLayoutDirection(nodes: Node[]): 'LR' | 'TB' {
    if (nodes.length < 2) return 'LR';

    // Calculate the spread in X vs Y
    const xs = nodes.map((n) => n.position.x);
    const ys = nodes.map((n) => n.position.y);

    const xSpread = Math.max(...xs) - Math.min(...xs);
    const ySpread = Math.max(...ys) - Math.min(...ys);

    // If horizontal spread is larger, keep LR; otherwise suggest TB
    return xSpread >= ySpread ? 'LR' : 'TB';
}
