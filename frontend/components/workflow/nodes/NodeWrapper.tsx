'use client';

import React, { memo } from 'react';
import { useWorkflowStore } from '@/lib/stores/workflow';
import { cn } from '@/lib/utils';

import { NodeInspector } from './NodeInspector';

interface NodeWrapperProps {
    id: string;
    selected?: boolean;
    className?: string;
    children: React.ReactNode;
    borderColor?: string;
}

export const NodeWrapper = memo(({ id, selected, className, children, borderColor = "border-gray-200" }: NodeWrapperProps) => {
    const activeNodeId = useWorkflowStore((state) => state.activeNodeId);
    const nodeStatus = useWorkflowStore((state) => state.nodeStatusMap[id] || 'pending');
    
    const isActive = activeNodeId === id;
    
    const statusClasses = {
        pending: '',
        running: 'border-blue-500 shadow-[0_0_15px_rgba(59,130,246,0.5)] animate-pulse',
        complete: 'border-green-500 shadow-[0_0_10px_rgba(34,197,94,0.3)]',
        error: 'border-red-500 shadow-[0_0_10px_rgba(239,68,68,0.3)]',
    };

    return (
        <NodeInspector id={id}>
            <div 
                className={cn(
                    "rounded-lg border-2 bg-card text-card-foreground transition-all duration-200",
                    borderColor,
                    selected && "ring-2 ring-primary ring-offset-2",
                    isActive && "scale-105 z-50",
                    statusClasses[nodeStatus],
                    className
                )}
            >
                {children}
                
                {/* Status Indicator Dot */}
                {nodeStatus !== 'pending' && (
                    <div className={cn(
                        "absolute -top-2 -right-2 w-4 h-4 rounded-full border-2 border-background z-50",
                        nodeStatus === 'running' && "bg-blue-500",
                        nodeStatus === 'complete' && "bg-green-500",
                        nodeStatus === 'error' && "bg-red-500"
                    )} />
                )}
            </div>
        </NodeInspector>
    );
});

NodeWrapper.displayName = 'NodeWrapper';
