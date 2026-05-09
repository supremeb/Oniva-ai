'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { Bell } from '@phosphor-icons/react';
import { BaseNode, NodeInfo } from './BaseNode';

export interface NotificationNodeData {
    label: string;
    channel: 'email' | 'slack' | 'teams' | 'sms' | 'webhook' | 'in_app';
    recipients: string;
    subject?: string;
    messageTemplate: string;
    priority: 'low' | 'normal' | 'high' | 'urgent';
    slackChannel?: string;
    teamsChannel?: string;
    webhookUrl?: string;
}

const channelLabels: Record<string, string> = {
    email: 'Email',
    slack: 'Slack',
    teams: 'MS Teams',
    sms: 'SMS',
    webhook: 'Webhook',
    in_app: 'In-App',
};

const priorityColors: Record<string, string> = {
    low: 'text-gray-500',
    normal: 'text-blue-500',
    high: 'text-orange-500',
    urgent: 'text-red-500',
};

function NotificationNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as NotificationNodeData;
    const channel = nodeData.channel || 'email';
    const hasError = !nodeData.recipients && !nodeData.slackChannel && !nodeData.webhookUrl;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="http"
            icon={<Bell className="h-3.5 w-3.5 text-white" weight="bold" />}
            title={nodeData.label || 'Notification'}
            category="Output"
            description={`Send via ${channelLabels[channel] || channel}`}
            hasError={hasError}
        >
            <NodeInfo
                label="Channel"
                value={channelLabels[channel] || channel}
            />
            {nodeData.recipients && (
                <NodeInfo
                    label="To"
                    value={nodeData.recipients.length > 30 ? `${nodeData.recipients.substring(0, 30)}...` : nodeData.recipients}
                />
            )}
            {nodeData.slackChannel && (
                <NodeInfo label="Channel" value={nodeData.slackChannel} />
            )}
            {nodeData.subject && (
                <NodeInfo label="Subject" value={nodeData.subject.substring(0, 25)} />
            )}
            <NodeInfo
                label="Priority"
                value={<span className={priorityColors[nodeData.priority || 'normal']}>{nodeData.priority || 'normal'}</span>}
            />
        </BaseNode>
    );
}

export const NotificationNode = memo(NotificationNodeComponent);
