'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { TextT } from '@phosphor-icons/react';

import { BaseNode, NodePromptPreview } from './BaseNode';

export interface TemplateNodeData {
    label: string;
    template?: string;
    variables?: string[];
    status?: 'idle' | 'running' | 'complete' | 'error';
}

function TemplateNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as TemplateNodeData;
    const templatePreview = nodeData.template
        ? nodeData.template.slice(0, 60) + (nodeData.template.length > 60 ? '...' : '')
        : '';
    const hasError = !nodeData.template;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="template"
            icon={<TextT className="h-3.5 w-3.5 text-white" weight="bold" />}
            title={nodeData.label || 'Template'}
            category="Transform"
            description="Format text with variables"
            hasError={hasError}
        >
            {templatePreview ? (
                <NodePromptPreview
                    label="Template"
                    content={templatePreview}
                    bgClass="bg-pink-50/70 border border-pink-100"
                />
            ) : (
                <div className="text-muted-foreground text-[11px] italic">
                    No template defined
                </div>
            )}

            {nodeData.variables && nodeData.variables.length > 0 && (
                <div>
                    <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">
                        Variables
                    </div>
                    <div className="flex flex-wrap gap-1">
                        {nodeData.variables.slice(0, 6).map((v, i) => (
                            <span
                                key={i}
                                className="px-1.5 py-0.5 bg-pink-100 rounded text-pink-700 text-[10px] font-medium border border-pink-200"
                            >
                                {'{{' + v + '}}'}
                            </span>
                        ))}
                        {nodeData.variables.length > 6 && (
                            <span className="px-1.5 py-0.5 text-muted-foreground text-[10px]">
                                +{nodeData.variables.length - 6}
                            </span>
                        )}
                    </div>
                </div>
            )}
        </BaseNode>
    );
}

export const TemplateNode = memo(TemplateNodeComponent);
