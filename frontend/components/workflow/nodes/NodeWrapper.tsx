'use client';

import React, { memo } from 'react';
import { useWorkflowStore } from '@/lib/stores/workflow';
import { cn } from '@/lib/utils';

import { NodeInspector } from './NodeInspector';
import { canvasDotClass, canvasRingClass, canvasStatusLabel } from '@/lib/run/canvasStatus';

interface NodeWrapperProps {
    id: string;
    selected?: boolean;
    className?: string;
    children: React.ReactNode;
    borderColor?: string;
}

export const NodeWrapper = memo(({ id, selected, className, children, borderColor = "border-gray-200" }: NodeWrapperProps) => {
    const activeNodeId = useWorkflowStore((state) => state.activeNodeId);
    const nodeStatus = useWorkflowStore((state) => state.nodeStatusMap[id]);
    
    const isActive = activeNodeId === id;
    
    return (
        <NodeInspector id={id}>
            <div 
                className={cn(
                    "rounded-lg border-2 bg-card text-card-foreground transition-all duration-200",
                    borderColor,
                    selected && "ring-2 ring-primary ring-offset-2",
                    isActive && "scale-105 z-50",
                    canvasRingClass(nodeStatus),
                    className
                )}
            >
                {children}
                
                {/* Status Indicator Dot */}
                {canvasDotClass(nodeStatus) && (
                    <div
                        data-testid="node-status-dot"
                        data-status={nodeStatus}
                        title={canvasStatusLabel(nodeStatus) ?? undefined}
                        className={cn("absolute -top-2 -right-2 w-4 h-4 rounded-full border-2 border-background z-50", canvasDotClass(nodeStatus))}
                    />
                )}
            </div>
        </NodeInspector>
    );
});

NodeWrapper.displayName = 'NodeWrapper';
