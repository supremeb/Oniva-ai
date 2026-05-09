'use client';

import { memo } from 'react';
import { type NodeProps, Handle, Position } from '@xyflow/react';
import { Link } from '@phosphor-icons/react';
import { BaseNode, NodeInfo } from './BaseNode';

export interface WebhookTriggerNodeData {
    label: string;
    endpointPath: string;
    authType: 'none' | 'hmac' | 'api_key' | 'oauth';
    responseMode: 'sync' | 'async';
    syncTimeoutSeconds: number;
    payloadSchema?: string;
    expectedContentType: 'json' | 'form' | 'xml' | 'text';
    rateLimitPerMinute: number;
    isActive: boolean;
}

const authTypeLabels: Record<string, string> = {
    none: 'No Auth',
    hmac: 'HMAC Signature',
    api_key: 'API Key',
    oauth: 'OAuth 2.0',
};

function WebhookTriggerNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as WebhookTriggerNodeData;
    const authType = nodeData.authType || 'hmac';
    const hasError = !nodeData.endpointPath;
    const isActive = nodeData.isActive !== false;

    return (
        <BaseNode
                id={id}
                selected={selected}
                nodeType="webhook_trigger"
                icon={<Link className="h-3.5 w-3.5 text-white" weight="bold" />}
                title={nodeData.label || 'Webhook Trigger'}
                category="Trigger"
                description="HTTP webhook endpoint"
                hasError={hasError}
                hasInput={false}
            >
                <NodeInfo
                    label="Endpoint"
                    value={nodeData.endpointPath ? `/webhooks/${nodeData.endpointPath}` : 'Not configured'}
                />
                <NodeInfo
                    label="Auth"
                    value={authTypeLabels[authType] || authType}
                />
                <NodeInfo
                    label="Mode"
                    value={nodeData.responseMode === 'sync' ? 'Synchronous' : 'Asynchronous'}
                />
                <NodeInfo
                    label="Status"
                    value={
                        <span className={isActive ? 'text-green-600' : 'text-gray-400'}>
                            {isActive ? 'Active' : 'Inactive'}
                        </span>
                    }
                />
        </BaseNode>
    );
}

export const WebhookTriggerNode = memo(WebhookTriggerNodeComponent);
