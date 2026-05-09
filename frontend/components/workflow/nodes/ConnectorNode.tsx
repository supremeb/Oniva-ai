'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { Plugs, Gear, Key, WarningCircle } from '@phosphor-icons/react';

import { BaseNode, NodeInfo, NodePromptPreview, NodeBadge } from './BaseNode';
import { getIntegrationInfo } from '@/lib/utils/integration-icons';

export interface ConnectorNodeData {
    label: string;
    connectorId?: string;
    connectorName?: string;
    connectorType?: 'mcp' | 'rest' | 'graphql';
    operation?: string;
    query?: string;
    inputs?: Record<string, string>;
    hasCredentials?: boolean;
}

const TYPE_VARIANTS: Record<string, 'default' | 'success' | 'warning' | 'error' | 'info'> = {
    mcp: 'info',
    rest: 'success',
    graphql: 'warning',
};

function ConnectorNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as ConnectorNodeData;
    const hasError = !nodeData.connectorId;
    const inputCount = nodeData.inputs ? Object.keys(nodeData.inputs).length : 0;

    // Get integration branding info
    const integrationInfo = getIntegrationInfo(nodeData.connectorName);
    const isMCP = nodeData.connectorType === 'mcp' || integrationInfo?.isMCP;
    const hasCredentials = nodeData.hasCredentials !== false; // Default to true unless explicitly false

    return (
        <div className="relative">
            {/* MCP Badge floating above node for MCP connectors */}
            {isMCP && (
                <div className="absolute -top-6 right-2 px-1.5 py-0.5 bg-emerald-100 text-emerald-700 text-[9px] font-bold rounded border border-emerald-200 z-10">
                    MCP
                </div>
            )}

            <BaseNode
                id={id}
                selected={selected}
                nodeType="connector"
                icon={integrationInfo?.icon || <Plugs className="h-3.5 w-3.5 text-white" weight="fill" />}
                title={nodeData.label || 'Connector'}
                category="Integration"
                description={nodeData.connectorName ? `Connect to ${nodeData.connectorName}` : 'External service integration'}
                hasError={hasError}
            >
                <NodeInfo
                    label="Connector"
                    value={
                        <span className="flex items-center gap-1.5">
                            {integrationInfo?.icon && (
                                <span className="flex-shrink-0">{integrationInfo.icon}</span>
                            )}
                            {nodeData.connectorName || <span className="text-red-500 text-[11px]">Not selected</span>}
                        </span>
                    }
                    valueClass="text-teal-700"
                />

                {nodeData.connectorType && (
                    <div className="flex items-center justify-between">
                        <span className="text-muted-foreground text-[11px] uppercase tracking-wide">Type</span>
                        <NodeBadge variant={TYPE_VARIANTS[nodeData.connectorType] || 'default'}>
                            {nodeData.connectorType.toUpperCase()}
                        </NodeBadge>
                    </div>
                )}

                {nodeData.operation && (
                    <NodeInfo
                        label="Operation"
                        value={nodeData.operation}
                        valueClass="font-mono text-muted-foreground"
                    />
                )}

                {nodeData.query && (
                    <NodePromptPreview
                        label="Query"
                        content={nodeData.query}
                        bgClass="bg-teal-50/50"
                    />
                )}

                {/* Credentials warning with action button */}
                {!hasCredentials && nodeData.connectorId && (
                    <div className="p-2 bg-red-50 border border-red-100 rounded-lg">
                        <div className="flex items-center gap-2 mb-2">
                            <WarningCircle className="h-4 w-4 text-red-500" weight="fill" />
                            <span className="text-[11px] text-red-600 font-medium">
                                Missing credentials
                            </span>
                        </div>
                        <button className="w-full px-3 py-1.5 bg-emerald-500 hover:bg-emerald-600 text-white text-[11px] font-medium rounded-md transition-colors flex items-center justify-center gap-1.5">
                            <Key className="h-3 w-3" weight="fill" />
                            Authenticate credentials
                        </button>
                    </div>
                )}

                {inputCount > 0 && hasCredentials && (
                    <NodeBadge variant="default">
                        <Gear className="h-3 w-3" weight="fill" />
                        {inputCount} input{inputCount > 1 ? 's' : ''} configured
                    </NodeBadge>
                )}
            </BaseNode>
        </div>
    );
}

export const ConnectorNode = memo(ConnectorNodeComponent);
