'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { CalendarBlank, Timer, Clock, ArrowClockwise } from '@phosphor-icons/react';
import { BaseNode, NodeInfo, NodeBadge } from './BaseNode';

export interface ScheduleTriggerNodeData {
    label: string;
    scheduleType: 'cron' | 'interval' | 'one_time';
    cronExpression?: string;
    intervalSeconds?: number;
    scheduledTime?: string;
    timezone: string;
    isEnabled: boolean;
    inputVariables?: Record<string, unknown>;
    maxExecutions?: number;
    retryOnFailure: boolean;
    maxRetries: number;
}

const scheduleTypeLabels: Record<string, string> = {
    cron: 'Cron Schedule',
    interval: 'Interval',
    one_time: 'One-Time',
};

const scheduleTypeIcons: Record<string, React.ReactNode> = {
    cron: <CalendarBlank className="h-3.5 w-3.5 text-white" weight="bold" />,
    interval: <ArrowClockwise className="h-3.5 w-3.5 text-white" weight="bold" />,
    one_time: <Timer className="h-3.5 w-3.5 text-white" weight="bold" />,
};

/**
 * Format cron expression to human-readable string
 */
function formatCronExpression(cron: string): string {
    const parts = cron.trim().split(/\s+/);
    if (parts.length !== 5) return cron;

    const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;

    // Common patterns
    if (cron === '* * * * *') return 'Every minute';
    if (cron === '0 * * * *') return 'Every hour';
    if (cron === '0 0 * * *') return 'Every day at midnight';
    if (cron === '0 9 * * 1-5') return 'Weekdays at 9:00 AM';
    if (cron === '0 0 1 * *') return 'First of every month';

    // Try to generate a meaningful description
    if (minute.startsWith('*/') && hour === '*' && dayOfMonth === '*') {
        return `Every ${minute.slice(2)} minutes`;
    }
    if (hour.startsWith('*/') && minute === '0' && dayOfMonth === '*') {
        return `Every ${hour.slice(2)} hours`;
    }
    if (minute !== '*' && hour !== '*' && dayOfMonth === '*' && month === '*') {
        const dayDesc = dayOfWeek === '*' ? 'daily' :
                       dayOfWeek === '1-5' ? 'weekdays' :
                       dayOfWeek === '0,6' ? 'weekends' : `on day ${dayOfWeek}`;
        return `${dayDesc} at ${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`;
    }

    return cron;
}

/**
 * Format interval seconds to human-readable string
 */
function formatInterval(seconds: number): string {
    if (seconds >= 86400) {
        const days = Math.floor(seconds / 86400);
        return `Every ${days} day${days > 1 ? 's' : ''}`;
    }
    if (seconds >= 3600) {
        const hours = Math.floor(seconds / 3600);
        return `Every ${hours} hour${hours > 1 ? 's' : ''}`;
    }
    if (seconds >= 60) {
        const minutes = Math.floor(seconds / 60);
        return `Every ${minutes} minute${minutes > 1 ? 's' : ''}`;
    }
    return `Every ${seconds} second${seconds !== 1 ? 's' : ''}`;
}

function ScheduleTriggerNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as ScheduleTriggerNodeData;
    const scheduleType = nodeData.scheduleType || 'cron';
    const isEnabled = nodeData.isEnabled !== false;

    // Determine schedule description
    let scheduleDescription = 'Not configured';
    if (scheduleType === 'cron' && nodeData.cronExpression) {
        scheduleDescription = formatCronExpression(nodeData.cronExpression);
    } else if (scheduleType === 'interval' && nodeData.intervalSeconds) {
        scheduleDescription = formatInterval(nodeData.intervalSeconds);
    } else if (scheduleType === 'one_time' && nodeData.scheduledTime) {
        const date = new Date(nodeData.scheduledTime);
        scheduleDescription = `Once at ${date.toLocaleString()}`;
    }

    const hasError = scheduleType === 'cron' ? !nodeData.cronExpression :
                     scheduleType === 'interval' ? !nodeData.intervalSeconds :
                     scheduleType === 'one_time' ? !nodeData.scheduledTime : true;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="schedule_trigger"
            icon={scheduleTypeIcons[scheduleType] || <Clock className="h-3.5 w-3.5 text-white" weight="bold" />}
            title={nodeData.label || 'Schedule Trigger'}
            category="Trigger"
            description="Cron/interval scheduling"
            hasError={hasError}
            hasInput={false}
            hasOutput={true}
        >
            <NodeInfo
                label="Type"
                value={<NodeBadge variant="default">{scheduleTypeLabels[scheduleType]}</NodeBadge>}
            />
            <NodeInfo
                label="Schedule"
                value={scheduleDescription}
            />
            <NodeInfo
                label="Timezone"
                value={nodeData.timezone || 'UTC'}
            />
            <NodeInfo
                label="Status"
                value={
                    <span className={isEnabled ? 'text-green-600' : 'text-gray-400'}>
                        {isEnabled ? 'Enabled' : 'Disabled'}
                    </span>
                }
            />
            {nodeData.retryOnFailure && (
                <NodeInfo
                    label="Retry"
                    value={`Up to ${nodeData.maxRetries || 3} times`}
                />
            )}
            {nodeData.maxExecutions && (
                <NodeInfo
                    label="Limit"
                    value={`${nodeData.maxExecutions} executions`}
                />
            )}
        </BaseNode>
    );
}

export const ScheduleTriggerNode = memo(ScheduleTriggerNodeComponent);
