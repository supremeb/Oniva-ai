'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { Equals } from '@phosphor-icons/react';
import { BaseNode, NodeInfo } from './BaseNode';

export interface VariableAssignment {
    name: string;
    value: string;
    type: 'string' | 'number' | 'boolean' | 'json' | 'array' | 'reference';
}

export interface VariableAssignerNodeData {
    label: string;
    assignments: VariableAssignment[];
    outputMode: 'merge' | 'replace';
}

function VariableAssignerNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as VariableAssignerNodeData;
    const assignments = nodeData.assignments || [];
    const hasError = assignments.length === 0;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="aggregator"
            icon={<Equals className="h-3.5 w-3.5 text-white" weight="bold" />}
            title={nodeData.label || 'Variable Assigner'}
            category="Transform"
            description={`Assign ${assignments.length} variable${assignments.length !== 1 ? 's' : ''}`}
            hasError={hasError}
        >
            <NodeInfo
                label="Assignments"
                value={assignments.length > 0 ? `${assignments.length} variable(s)` : 'None'}
            />
            {assignments.slice(0, 3).map((assignment, i) => (
                <NodeInfo
                    key={i}
                    label={assignment.name || 'unnamed'}
                    value={assignment.type === 'reference' ? `{{${assignment.value}}}` : assignment.value?.substring(0, 20) || '(empty)'}
                />
            ))}
            {assignments.length > 3 && (
                <NodeInfo label="" value={`+${assignments.length - 3} more`} />
            )}
        </BaseNode>
    );
}

export const VariableAssignerNode = memo(VariableAssignerNodeComponent);
