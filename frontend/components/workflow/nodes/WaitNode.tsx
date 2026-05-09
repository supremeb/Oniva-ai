'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { Timer } from '@phosphor-icons/react';
import { BaseNode, NodeInfo } from './BaseNode';

export interface WaitNodeData {
    label: string;
    waitType: 'duration' | 'until_time' | 'business_hours';
    duration?: number;
    durationUnit?: 'seconds' | 'minutes' | 'hours' | 'days';
    targetTime?: string;
    timezone?: string;
    businessHoursConfig?: {
        startHour: number;
        endHour: number;
        weekdays: number[];
    };
}

function WaitNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as WaitNodeData;
    const waitType = nodeData.waitType || 'duration';
    const hasError = waitType === 'duration' && !nodeData.duration;

    const getWaitDescription = () => {
        switch (waitType) {
            case 'duration':
                if (nodeData.duration) {
                    const unit = nodeData.durationUnit || 'seconds';
                    return `${nodeData.duration} ${unit}`;
                }
                return 'Not configured';
            case 'until_time':
                return nodeData.targetTime || 'No time set';
            case 'business_hours':
                return 'Next business hours';
            default:
                return 'Unknown';
        }
    };

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="wait"
            icon={<Timer className="h-3.5 w-3.5 text-white" weight="bold" />}
            title={nodeData.label || 'Wait'}
            category="Logic"
            description={waitType === 'duration' ? 'Pause execution' : 'Wait for time'}
            hasError={hasError}
        >
            <NodeInfo
                label="Type"
                value={waitType === 'duration' ? 'Duration' : waitType === 'until_time' ? 'Until Time' : 'Business Hours'}
            />
            <NodeInfo label="Wait" value={getWaitDescription()} />
        </BaseNode>
    );
}

export const WaitNode = memo(WaitNodeComponent);
