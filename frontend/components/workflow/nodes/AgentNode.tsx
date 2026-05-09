'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { Robot, Sparkle, Plugs } from '@phosphor-icons/react';
import { useAgents } from '@/hooks/useAgents';

import { BaseNode, NodeInfo, NodePromptPreview, NodeBadge } from './BaseNode';

export interface AgentNodeData {
    label: string;
    agentId?: string;
    agentName?: string;
    agentType?: string;
    userPrompt?: string;
    model?: string;
    skills?: string[];
    connectors?: string[];
}

function AgentNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as AgentNodeData;
    const { data: agents = [] } = useAgents();

    // Look up agent name from agents list if not provided directly
    const selectedAgent = nodeData.agentId ? agents.find((a) => a.id === nodeData.agentId) : null;
    const agentName = nodeData.agentName || selectedAgent?.name;
    const hasError = !nodeData.agentId;
    const hasSkills = nodeData.skills && nodeData.skills.length > 0;
    const hasConnectors = nodeData.connectors && nodeData.connectors.length > 0;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="agent"
            icon={<Robot className="h-3.5 w-3.5 text-white" weight="fill" />}
            title={nodeData.label || 'Agent'}
            category="AI"
            description={agentName || 'Execute AI agent'}
            hasError={hasError}
        >
            <NodeInfo
                label="Agent"
                value={agentName || <span className="text-red-500 text-[11px]">Not selected</span>}
                valueClass="text-indigo-700"
            />

            {nodeData.model && (
                <NodeInfo
                    label="Model"
                    value={nodeData.model}
                    valueClass="text-muted-foreground"
                />
            )}

            {nodeData.userPrompt && (
                <NodePromptPreview
                    label="Prompt"
                    content={nodeData.userPrompt}
                    bgClass="bg-indigo-50"
                />
            )}

            {(hasSkills || hasConnectors) && (
                <div className="flex items-center gap-2 pt-1">
                    {hasSkills && (
                        <NodeBadge variant="success">
                            <Sparkle className="h-3 w-3" weight="fill" />
                            {nodeData.skills!.length} skill{nodeData.skills!.length > 1 ? 's' : ''}
                        </NodeBadge>
                    )}
                    {hasConnectors && (
                        <NodeBadge variant="info">
                            <Plugs className="h-3 w-3" weight="fill" />
                            {nodeData.connectors!.length} connector{nodeData.connectors!.length > 1 ? 's' : ''}
                        </NodeBadge>
                    )}
                </div>
            )}
        </BaseNode>
    );
}

export const AgentNode = memo(AgentNodeComponent);
