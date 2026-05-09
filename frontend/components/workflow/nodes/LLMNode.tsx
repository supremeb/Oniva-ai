'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { Brain, BookOpen, Sparkle } from '@phosphor-icons/react';

import { BaseNode, NodeInfo, NodePromptPreview, NodeBadge } from './BaseNode';
import { getModelById } from '@/lib/models';

export interface LLMNodeData {
    label: string;
    model?: string;
    systemPrompt?: string;
    userPrompt?: string;
    temperature?: number;
    maxOutputTokens?: number;
    context?: string[];
    skills?: string[];
}

function LLMNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as LLMNodeData;
    const model = nodeData.model ? getModelById(nodeData.model) : undefined;
    const hasError = !nodeData.model;
    const hasSkills = nodeData.skills && nodeData.skills.length > 0;
    const hasContext = nodeData.context && nodeData.context.length > 0;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="llm"
            icon={<Brain className="h-3.5 w-3.5 text-white" weight="fill" />}
            title={nodeData.label || 'LLM'}
            category="AI"
            description={model ? `Generate with ${model.name.split(' ')[0]}` : 'AI text generation'}
            hasError={hasError}
        >
            <NodeInfo
                label="Model"
                value={model ? model.name : <span className="text-red-500 text-[11px]">Not selected</span>}
                valueClass="text-violet-700"
            />

            {nodeData.systemPrompt && (
                <NodePromptPreview
                    label="System"
                    content={nodeData.systemPrompt}
                    bgClass="bg-slate-50"
                />
            )}

            {nodeData.userPrompt && (
                <NodePromptPreview
                    label="User"
                    content={nodeData.userPrompt}
                    bgClass="bg-violet-50"
                />
            )}

            {(hasContext || hasSkills) && (
                <div className="flex items-center gap-2 pt-1">
                    {hasContext && (
                        <NodeBadge variant="info">
                            <BookOpen className="h-3 w-3" weight="fill" />
                            {nodeData.context!.length} context
                        </NodeBadge>
                    )}
                    {hasSkills && (
                        <NodeBadge variant="success">
                            <Sparkle className="h-3 w-3" weight="fill" />
                            {nodeData.skills!.length} skill{nodeData.skills!.length > 1 ? 's' : ''}
                        </NodeBadge>
                    )}
                </div>
            )}
        </BaseNode>
    );
}

export const LLMNode = memo(LLMNodeComponent);
