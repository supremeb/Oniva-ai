'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { Globe, ArrowRight, Clock } from '@phosphor-icons/react';

import { BaseNode, NodeInfo, NodeBadge } from './BaseNode';

export interface HttpRequestNodeData {
    label: string;
    method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
    url?: string;
    headers?: Record<string, string>;
    body?: string;
    timeout?: number;
    status?: 'idle' | 'running' | 'complete' | 'error';
}

const METHOD_VARIANTS: Record<string, 'success' | 'info' | 'warning' | 'error' | 'default'> = {
    GET: 'success',
    POST: 'info',
    PUT: 'warning',
    DELETE: 'error',
    PATCH: 'default',
};

function HttpRequestNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as HttpRequestNodeData;
    const hasError = !nodeData.url;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="http"
            icon={<Globe className="h-3.5 w-3.5 text-white" weight="fill" />}
            title={nodeData.label || 'HTTP Request'}
            category="Integration"
            description={`Make ${nodeData.method || 'GET'} request`}
            hasError={hasError}
        >
            <div className="flex items-center justify-between">
                <span className="text-muted-foreground text-[11px] uppercase tracking-wide">Method</span>
                <NodeBadge variant={METHOD_VARIANTS[nodeData.method || 'GET']}>
                    {nodeData.method || 'GET'}
                </NodeBadge>
            </div>

            {nodeData.url ? (
                <div className="p-2 bg-orange-50 rounded-lg border border-orange-100 flex items-center gap-2">
                    <ArrowRight className="h-3.5 w-3.5 text-orange-500 shrink-0" weight="bold" />
                    <span className="truncate font-mono text-[10px] text-orange-700">{nodeData.url}</span>
                </div>
            ) : (
                <div className="text-muted-foreground text-[11px] italic">
                    No URL configured
                </div>
            )}

            <div className="flex items-center gap-2">
                {nodeData.timeout && (
                    <NodeBadge variant="default">
                        <Clock className="h-3 w-3" />
                        {nodeData.timeout}s
                    </NodeBadge>
                )}
                {nodeData.headers && Object.keys(nodeData.headers).length > 0 && (
                    <NodeBadge variant="default">
                        {Object.keys(nodeData.headers).length} header{Object.keys(nodeData.headers).length > 1 ? 's' : ''}
                    </NodeBadge>
                )}
                {nodeData.body && (
                    <NodeBadge variant="info">
                        Body
                    </NodeBadge>
                )}
            </div>
        </BaseNode>
    );
}

export const HttpRequestNode = memo(HttpRequestNodeComponent);
