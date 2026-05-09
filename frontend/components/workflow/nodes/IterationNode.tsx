'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { ArrowsClockwise } from '@phosphor-icons/react';

import { BaseNode, NodeInfo, NodeBadge } from './BaseNode';

export interface IterationNodeData {
    label: string;
    iteratorVariable?: string;
    outputVariable?: string;
    parallelMode?: boolean;
    maxParallel?: number;
    status?: 'idle' | 'running' | 'complete' | 'error';
    currentIteration?: number;
    totalIterations?: number;
}

function IterationNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as IterationNodeData;
    const hasError = !nodeData.iteratorVariable;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="iteration"
            icon={<ArrowsClockwise className="h-3.5 w-3.5 text-white" weight="bold" />}
            title={nodeData.label || 'Iteration'}
            category="Logic"
            description="Loop over a collection"
            hasError={hasError}
        >
            {nodeData.iteratorVariable ? (
                <div className="p-2 bg-cyan-50 rounded-lg border border-cyan-100">
                    <span className="text-muted-foreground text-[10px]">Iterate over:</span>{' '}
                    <span className="font-semibold text-cyan-700">{nodeData.iteratorVariable}</span>
                </div>
            ) : (
                <div className="text-muted-foreground text-[11px] italic">
                    No iterator variable configured
                </div>
            )}

            <div className="flex items-center gap-2">
                {nodeData.parallelMode && (
                    <NodeBadge variant="info">
                        Parallel
                        {nodeData.maxParallel && <span className="ml-0.5">({nodeData.maxParallel})</span>}
                    </NodeBadge>
                )}
            </div>

            {nodeData.status === 'running' && nodeData.totalIterations && (
                <div className="p-2 bg-blue-50 rounded-lg border border-blue-200">
                    <div className="flex items-center justify-between text-[11px]">
                        <span className="text-blue-600">Progress</span>
                        <span className="font-bold text-blue-700">
                            {nodeData.currentIteration || 0} / {nodeData.totalIterations}
                        </span>
                    </div>
                    <div className="mt-1.5 h-1.5 bg-blue-200 rounded-full overflow-hidden">
                        <div
                            className="h-full bg-blue-500 rounded-full transition-all duration-300"
                            style={{
                                width: `${((nodeData.currentIteration || 0) / nodeData.totalIterations) * 100}%`
                            }}
                        />
                    </div>
                </div>
            )}

            {nodeData.outputVariable && (
                <NodeInfo
                    label="Output"
                    value={<span className="text-cyan-700">{nodeData.outputVariable}</span>}
                />
            )}
        </BaseNode>
    );
}

export const IterationNode = memo(IterationNodeComponent);
