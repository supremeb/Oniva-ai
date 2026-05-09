'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { Books } from '@phosphor-icons/react';

import { BaseNode, NodeInfo, NodeBadge } from './BaseNode';

export interface KnowledgeNodeData {
    label: string;
    knowledgeBaseId?: string;
    knowledgeBaseName?: string;
    topK?: number;
    scoreThreshold?: number;
    rerankModel?: string;
}

function KnowledgeNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as KnowledgeNodeData;
    const hasError = !nodeData.knowledgeBaseId;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="knowledge"
            icon={<Books className="h-3.5 w-3.5 text-white" weight="fill" />}
            title={nodeData.label || 'Knowledge Retrieval'}
            category="Data"
            description="Retrieve from knowledge base"
            hasError={hasError}
        >
            {nodeData.knowledgeBaseName ? (
                <div className="p-2 bg-amber-50 rounded-lg border border-amber-100">
                    <div className="text-[11px] font-medium text-amber-700 flex items-center gap-1.5">
                        <Books className="h-3 w-3" weight="fill" />
                        {nodeData.knowledgeBaseName}
                    </div>
                </div>
            ) : (
                <div className="text-muted-foreground text-[11px] italic py-1">
                    No knowledge base selected
                </div>
            )}

            <div className="flex items-center gap-2">
                {nodeData.topK && (
                    <NodeBadge variant="default">
                        Top {nodeData.topK}
                    </NodeBadge>
                )}
                {nodeData.scoreThreshold && (
                    <NodeBadge variant="default">
                        Threshold: {nodeData.scoreThreshold}
                    </NodeBadge>
                )}
            </div>
        </BaseNode>
    );
}

export const KnowledgeNode = memo(KnowledgeNodeComponent);
