'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { BookOpenText, Files, MagnifyingGlass, Quotes } from '@phosphor-icons/react';

import { BaseNode, NodePromptPreview, NodeBadge } from './BaseNode';

export interface ResearchNodeData {
    label: string;
    query?: string;
    documentIds?: string[];
    knowledgeBases?: string[];
    maxResults?: number;
    minConfidence?: number;
    includeCitations?: boolean;
    status?: 'idle' | 'running' | 'complete' | 'error';
    result?: {
        answer?: string;
        sources?: string[];
        confidence?: number;
    };
}

function ResearchNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as ResearchNodeData;
    const hasError = !nodeData.query && !nodeData.documentIds?.length && !nodeData.knowledgeBases?.length;
    const sourceCount = (nodeData.documentIds?.length || 0) + (nodeData.knowledgeBases?.length || 0);

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="research"
            icon={<BookOpenText className="h-3.5 w-3.5 text-white" weight="fill" />}
            title={nodeData.label || 'Research'}
            category="AI"
            description="RAG-based research"
            hasError={hasError}
        >
            {nodeData.query && (
                <div className="p-2 bg-rose-50/50 rounded-lg border border-rose-100">
                    <div className="flex items-center gap-1 text-muted-foreground mb-1">
                        <MagnifyingGlass className="h-3 w-3" />
                        <span className="text-[10px] font-semibold uppercase tracking-wide">Query</span>
                    </div>
                    <div className="line-clamp-2 text-[11px] leading-relaxed">{nodeData.query}</div>
                </div>
            )}

            <div className="flex items-center gap-2">
                {sourceCount > 0 && (
                    <NodeBadge variant="info">
                        <Files className="h-3 w-3" />
                        {sourceCount} source{sourceCount > 1 ? 's' : ''}
                    </NodeBadge>
                )}
                {nodeData.includeCitations && (
                    <NodeBadge variant="default">
                        <Quotes className="h-3 w-3" />
                        Citations
                    </NodeBadge>
                )}
            </div>

            {nodeData.result?.answer && (
                <div className="p-2 bg-emerald-50 rounded-lg border border-emerald-200">
                    <div className="text-emerald-700 font-semibold text-[10px] mb-1">Answer</div>
                    <div className="line-clamp-2 text-[11px] text-foreground">{nodeData.result.answer}</div>
                    <div className="flex items-center gap-2 mt-2 pt-2 border-t border-emerald-200">
                        {nodeData.result.sources && (
                            <span className="text-[10px] text-emerald-600">
                                {nodeData.result.sources.length} source{nodeData.result.sources.length > 1 ? 's' : ''}
                            </span>
                        )}
                        {nodeData.result.confidence !== undefined && (
                            <span className="text-[10px] text-emerald-600">
                                {(nodeData.result.confidence * 100).toFixed(0)}% confidence
                            </span>
                        )}
                    </div>
                </div>
            )}
        </BaseNode>
    );
}

export const ResearchNode = memo(ResearchNodeComponent);
