'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { FunnelSimple, ArrowRight } from '@phosphor-icons/react';

import { BaseNode, NodeInfo, NodeBadge } from './BaseNode';

export interface VariableAggregatorNodeData {
    label: string;
    inputVariables?: string[];
    outputVariable?: string;
    aggregationType?: 'concat' | 'first_non_empty' | 'array';
    status?: 'idle' | 'running' | 'complete' | 'error';
}

const AGGREGATION_LABELS: Record<string, string> = {
    concat: 'Concatenate',
    first_non_empty: 'First Non-Empty',
    array: 'To Array',
};

function VariableAggregatorNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as VariableAggregatorNodeData;
    const hasError = !nodeData.inputVariables?.length || !nodeData.outputVariable;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="aggregator"
            icon={<FunnelSimple className="h-3.5 w-3.5 text-white" weight="bold" />}
            title={nodeData.label || 'Variable Aggregator'}
            category="Transform"
            description="Combine multiple variables"
            hasError={hasError}
        >
            {nodeData.inputVariables && nodeData.inputVariables.length > 0 ? (
                <div>
                    <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
                        Inputs
                    </div>
                    <div className="flex flex-wrap gap-1">
                        {nodeData.inputVariables.slice(0, 5).map((v, i) => (
                            <span
                                key={i}
                                className="px-1.5 py-0.5 bg-teal-50 rounded text-teal-700 text-[10px] font-medium border border-teal-100"
                            >
                                {v}
                            </span>
                        ))}
                        {nodeData.inputVariables.length > 5 && (
                            <span className="px-1.5 py-0.5 text-muted-foreground text-[10px]">
                                +{nodeData.inputVariables.length - 5}
                            </span>
                        )}
                    </div>
                </div>
            ) : (
                <div className="text-muted-foreground text-[11px] italic">
                    No input variables
                </div>
            )}

            {nodeData.aggregationType && (
                <NodeInfo
                    label="Mode"
                    value={
                        <NodeBadge variant="default">
                            {AGGREGATION_LABELS[nodeData.aggregationType] || nodeData.aggregationType}
                        </NodeBadge>
                    }
                />
            )}

            {nodeData.outputVariable && (
                <div className="p-2 bg-emerald-50 rounded-lg border border-emerald-200 flex items-center gap-2">
                    <ArrowRight className="h-3.5 w-3.5 text-emerald-500" weight="bold" />
                    <span className="font-semibold text-[11px] text-emerald-700">
                        {nodeData.outputVariable}
                    </span>
                </div>
            )}
        </BaseNode>
    );
}

export const VariableAggregatorNode = memo(VariableAggregatorNodeComponent);
