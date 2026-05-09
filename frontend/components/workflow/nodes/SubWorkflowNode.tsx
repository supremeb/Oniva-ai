'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { TreeStructure } from '@phosphor-icons/react';
import { BaseNode, NodeInfo } from './BaseNode';

export interface InputMapping {
    workflowVar: string;
    sourceValue: string;
}

export interface SubWorkflowNodeData {
    label: string;
    workflowId: string;
    workflowName?: string;
    inputMappings: InputMapping[];
    outputVariable: string;
    async: boolean;
    timeout: number;
    failOnError: boolean;
}

function SubWorkflowNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as SubWorkflowNodeData;
    const hasError = !nodeData.workflowId;
    const mappingsCount = nodeData.inputMappings?.length || 0;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="iteration"
            icon={<TreeStructure className="h-3.5 w-3.5 text-white" weight="bold" />}
            title={nodeData.label || 'Sub-Workflow'}
            category="Logic"
            description={nodeData.workflowName || 'Execute nested workflow'}
            hasError={hasError}
        >
            <NodeInfo
                label="Workflow"
                value={nodeData.workflowName || nodeData.workflowId || 'Not selected'}
            />
            {mappingsCount > 0 && (
                <NodeInfo
                    label="Inputs"
                    value={`${mappingsCount} mapped`}
                />
            )}
            <NodeInfo
                label="Mode"
                value={nodeData.async ? 'Async' : 'Sync'}
            />
            {!nodeData.async && nodeData.timeout && (
                <NodeInfo
                    label="Timeout"
                    value={`${nodeData.timeout}s`}
                />
            )}
        </BaseNode>
    );
}

export const SubWorkflowNode = memo(SubWorkflowNodeComponent);
