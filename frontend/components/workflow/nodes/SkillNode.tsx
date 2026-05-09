'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { Toolbox, Gear } from '@phosphor-icons/react';

import { BaseNode, NodeInfo, NodeBadge } from './BaseNode';

export interface SkillNodeData {
    label: string;
    skillName?: string;
    skillCategory?: 'web' | 'code' | 'file' | 'memory' | 'mcp';
    skillDescription?: string;
    skillConfig?: Record<string, unknown>;
    inputs?: Record<string, string>;
}

const CATEGORY_VARIANTS: Record<string, 'default' | 'success' | 'warning' | 'error' | 'info'> = {
    web: 'info',
    code: 'success',
    file: 'warning',
    memory: 'default',
    mcp: 'info',
};

function SkillNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as SkillNodeData;
    const hasError = !nodeData.skillName;
    const inputCount = nodeData.inputs ? Object.keys(nodeData.inputs).length : 0;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="skill"
            icon={<Toolbox className="h-3.5 w-3.5 text-white" weight="fill" />}
            title={nodeData.label || 'Skill'}
            category="Utility"
            description={nodeData.skillName || 'Execute a skill'}
            hasError={hasError}
        >
            <NodeInfo
                label="Skill"
                value={nodeData.skillName || <span className="text-red-500 text-[11px]">Not selected</span>}
                valueClass="text-green-700"
            />

            {nodeData.skillCategory && (
                <div className="flex items-center justify-between">
                    <span className="text-muted-foreground text-[11px] uppercase tracking-wide">Category</span>
                    <NodeBadge variant={CATEGORY_VARIANTS[nodeData.skillCategory] || 'default'}>
                        {nodeData.skillCategory}
                    </NodeBadge>
                </div>
            )}

            {nodeData.skillDescription && (
                <div className="p-2 bg-green-50/50 rounded-lg border border-green-100">
                    <div className="text-[11px] line-clamp-2 text-muted-foreground leading-relaxed">
                        {nodeData.skillDescription}
                    </div>
                </div>
            )}

            {inputCount > 0 && (
                <NodeBadge variant="default">
                    <Gear className="h-3 w-3" weight="fill" />
                    {inputCount} input{inputCount > 1 ? 's' : ''} configured
                </NodeBadge>
            )}
        </BaseNode>
    );
}

export const SkillNode = memo(SkillNodeComponent);
