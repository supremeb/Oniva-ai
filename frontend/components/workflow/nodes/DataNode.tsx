'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { Database, CloudArrowDown } from '@phosphor-icons/react';

import { BaseNode, NodeInfo, NodePromptPreview, NodeBadge } from './BaseNode';

export interface DataNodeData {
    label: string;
    connectorId?: string;
    connectorName?: string;
    query?: string;
    outputFormat?: string;
    enableCache?: boolean;
    status?: 'idle' | 'running' | 'complete' | 'error';
    result?: unknown;
}

function DataNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as DataNodeData;
    const hasError = !nodeData.connectorId;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="data"
            icon={<Database className="h-3.5 w-3.5 text-white" weight="fill" />}
            title={nodeData.label || 'Data Retrieval'}
            category="Data"
            description={nodeData.connectorName || 'Fetch external data'}
            hasError={hasError}
        >
            <NodeInfo
                label="Source"
                value={nodeData.connectorName || <span className="text-red-500 text-[11px]">Not selected</span>}
                valueClass="text-blue-700"
            />

            {nodeData.outputFormat && (
                <div className="flex items-center justify-between">
                    <span className="text-muted-foreground text-[11px] uppercase tracking-wide">Format</span>
                    <NodeBadge variant="info">
                        {nodeData.outputFormat.toUpperCase()}
                    </NodeBadge>
                </div>
            )}

            {nodeData.query && (
                <NodePromptPreview
                    label="Query"
                    content={nodeData.query}
                    bgClass="bg-blue-50/50"
                />
            )}

            {nodeData.enableCache && (
                <NodeBadge variant="success">
                    <CloudArrowDown className="h-3 w-3" weight="fill" />
                    Caching enabled
                </NodeBadge>
            )}
        </BaseNode>
    );
}

export const DataNode = memo(DataNodeComponent);
