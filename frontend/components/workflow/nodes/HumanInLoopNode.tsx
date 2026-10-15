'use client';

import { memo } from 'react';
import { type NodeProps, Handle, Position } from '@xyflow/react';
import { UserCircle } from '@phosphor-icons/react';
import { BaseNode, NodeInfo } from './BaseNode';

export interface ApprovalOption {
    id: string;
    label: string;
    color?: string;
}

export interface HumanInLoopNodeData {
    label: string;
    title: string;
    description?: string;
    approverType: 'user' | 'role' | 'group' | 'dynamic';
    approverConfig: {
        userIds?: string[];
        roleIds?: string[];
        groupIds?: string[];
        dynamicExpression?: string;
    };
    options: ApprovalOption[];
    slaHours?: number;
    escalationEnabled: boolean;
    escalationConfig?: {
        afterHours: number;
        escalateTo: string;
    };
    requireComment: boolean;
}

const approverTypeLabels: Record<string, string> = {
    user: 'Specific Users',
    role: 'By Role',
    group: 'By Group',
    dynamic: 'Dynamic',
};

function HumanInLoopNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as HumanInLoopNodeData;
    const approverType = nodeData.approverType || 'user';
    const options = nodeData.options || [
        { id: 'approve', label: 'Approve' },
        { id: 'reject', label: 'Reject' },
    ];
    const hasError = !nodeData.title || options.length < 2;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="compliance"
            icon={<UserCircle className="h-3.5 w-3.5 text-white" weight="bold" />}
            title={nodeData.label || 'Human Approval'}
            category="Logic"
            description="Require human approval"
            hasError={hasError}
            multipleOutputs={options.map((opt) => ({
                id: opt.id,
                label: opt.label,
            }))}
        >
            <div
                data-testid="approval-simulated-badge"
                className="mb-1 inline-block rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800"
            >
                Simulated — approvals auto-approve
            </div>
            <NodeInfo
                label="Approver"
                value={approverTypeLabels[approverType] || approverType}
            />
            {nodeData.title && (
                <NodeInfo
                    label="Task"
                    value={nodeData.title.length > 25 ? `${nodeData.title.substring(0, 25)}...` : nodeData.title}
                />
            )}
            {nodeData.slaHours && (
                <NodeInfo
                    label="SLA"
                    value={`${nodeData.slaHours}h`}
                />
            )}
            <NodeInfo
                label="Options"
                value={options.map(o => o.label).join(', ')}
            />
        </BaseNode>
    );
}

export const HumanInLoopNode = memo(HumanInLoopNodeComponent);
