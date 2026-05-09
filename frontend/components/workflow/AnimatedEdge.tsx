'use client';

import { memo } from 'react';
import {
    BaseEdge,
    getBezierPath,
    type EdgeProps,
} from '@xyflow/react';
import { useWorkflowStore } from '@/lib/stores/workflow';

/**
 * Custom animated edge that:
 * - Shows flowing dots animation during workflow execution
 * - Highlights on hover for better visibility
 * - Uses smooth bezier curves for connected feel
 */
function AnimatedEdgeComponent({
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    style,
    markerEnd,
    source,
    target,
}: EdgeProps) {
    const isExecuting = useWorkflowStore((state) => state.isExecuting);
    const sourceNodeStatus = useWorkflowStore((state) => state.nodeStatusMap[source]);
    const targetNodeStatus = useWorkflowStore((state) => state.nodeStatusMap[target]);

    // Check if this edge is part of the active execution path
    // Use string comparison to avoid TypeScript narrowing issues
    const isSourceRunning = sourceNodeStatus === 'running';
    const isTargetRunning = targetNodeStatus === 'running';
    const isSourceComplete = sourceNodeStatus === 'complete';

    const isActiveEdge = isExecuting && (
        isSourceRunning ||
        isTargetRunning ||
        (isSourceComplete && isTargetRunning)
    );

    // Check if the source node has completed (edge should show as "done")
    const isCompletedEdge = isSourceComplete;

    const [edgePath] = getBezierPath({
        sourceX,
        sourceY,
        sourcePosition,
        targetX,
        targetY,
        targetPosition,
    });

    // Dynamic edge styling based on state
    const edgeColor = isActiveEdge
        ? '#6366f1' // Indigo for active
        : isCompletedEdge
            ? '#22c55e' // Green for completed path
            : '#94a3b8'; // Slate gray for inactive

    const strokeWidth = isActiveEdge ? 2.5 : 2;

    return (
        <>
            {/* Base edge with hover effect */}
            <BaseEdge
                id={id}
                path={edgePath}
                markerEnd={markerEnd}
                style={{
                    ...style,
                    stroke: edgeColor,
                    strokeWidth,
                    transition: 'stroke 0.3s ease, stroke-width 0.2s ease',
                }}
                className="hover:!stroke-indigo-500 hover:!stroke-[3px] transition-all"
            />

            {/* Animated flowing dots during execution */}
            {isActiveEdge && (
                <>
                    <circle r="4" fill="#6366f1">
                        <animateMotion
                            dur="1s"
                            repeatCount="indefinite"
                            path={edgePath}
                        />
                    </circle>
                    <circle r="4" fill="#6366f1" opacity="0.5">
                        <animateMotion
                            dur="1s"
                            repeatCount="indefinite"
                            path={edgePath}
                            begin="0.5s"
                        />
                    </circle>
                </>
            )}

            {/* Invisible wider path for easier hover targeting */}
            <path
                d={edgePath}
                fill="none"
                stroke="transparent"
                strokeWidth={20}
                className="cursor-pointer"
            />
        </>
    );
}

export const AnimatedEdge = memo(AnimatedEdgeComponent);
