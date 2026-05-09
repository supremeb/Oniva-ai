'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { Crosshair } from '@phosphor-icons/react';
import { BaseNode, NodeInfo } from './BaseNode';

export interface ParameterDefinition {
    name: string;
    type: 'string' | 'number' | 'boolean' | 'date' | 'array' | 'object';
    description?: string;
    required: boolean;
    defaultValue?: string;
    extractionHint?: string;
}

export interface ParameterExtractorNodeData {
    label: string;
    inputText: string;
    parameters: ParameterDefinition[];
    model?: string;
    instructions?: string;
    strictMode: boolean;
}

function ParameterExtractorNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as ParameterExtractorNodeData;
    const parameters = nodeData.parameters || [];
    const hasError = !nodeData.inputText || parameters.length === 0;

    const requiredCount = parameters.filter(p => p.required).length;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="classifier"
            icon={<Crosshair className="h-3.5 w-3.5 text-white" weight="bold" />}
            title={nodeData.label || 'Parameter Extractor'}
            category="Transform"
            description={`Extract ${parameters.length} parameter${parameters.length !== 1 ? 's' : ''}`}
            hasError={hasError}
        >
            <NodeInfo
                label="Parameters"
                value={parameters.length > 0 ? `${parameters.length} defined` : 'None'}
            />
            {requiredCount > 0 && (
                <NodeInfo
                    label="Required"
                    value={`${requiredCount} of ${parameters.length}`}
                />
            )}
            {parameters.slice(0, 3).map((param, i) => (
                <NodeInfo
                    key={i}
                    label={param.name || 'unnamed'}
                    value={`${param.type}${param.required ? '*' : ''}`}
                />
            ))}
            {parameters.length > 3 && (
                <NodeInfo label="" value={`+${parameters.length - 3} more`} />
            )}
            {nodeData.model && (
                <NodeInfo label="Model" value={nodeData.model.split('/').pop() || nodeData.model} />
            )}
        </BaseNode>
    );
}

export const ParameterExtractorNode = memo(ParameterExtractorNodeComponent);
