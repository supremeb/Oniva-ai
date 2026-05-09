'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { Code } from '@phosphor-icons/react';

import { BaseNode, NodeBadge } from './BaseNode';

export interface CodeNodeData {
    label: string;
    language: 'python3' | 'javascript';
    code?: string;
    inputVariables?: string[];
    outputVariables?: string[];
    status?: 'idle' | 'running' | 'complete' | 'error';
}

function CodeNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as CodeNodeData;
    const codePreview = nodeData.code ? nodeData.code.slice(0, 60) + (nodeData.code.length > 60 ? '...' : '') : '';
    const hasInputs = nodeData.inputVariables && nodeData.inputVariables.length > 0;
    const hasOutputs = nodeData.outputVariables && nodeData.outputVariables.length > 0;

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="code"
            icon={<Code className="h-3.5 w-3.5 text-white" weight="bold" />}
            title={nodeData.label || 'Code'}
            category="Transform"
            description={`Execute ${nodeData.language === 'javascript' ? 'JavaScript' : 'Python'} code`}
        >
            {/* Language Badge */}
            <div className="flex items-center justify-between">
                <span className="text-muted-foreground text-[11px] uppercase tracking-wide">Language</span>
                <NodeBadge variant={nodeData.language === 'python3' ? 'warning' : 'info'}>
                    {nodeData.language === 'javascript' ? 'JavaScript' : 'Python'}
                </NodeBadge>
            </div>

            {/* Input Variables */}
            {hasInputs && (
                <div>
                    <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">Inputs</div>
                    <div className="flex flex-wrap gap-1">
                        {nodeData.inputVariables!.slice(0, 4).map((v, i) => (
                            <span key={i} className="px-1.5 py-0.5 bg-sky-50 rounded text-sky-700 text-[10px] font-medium border border-sky-100">
                                {v}
                            </span>
                        ))}
                        {nodeData.inputVariables!.length > 4 && (
                            <span className="px-1.5 py-0.5 text-muted-foreground text-[10px]">
                                +{nodeData.inputVariables!.length - 4}
                            </span>
                        )}
                    </div>
                </div>
            )}

            {/* Code Preview */}
            {codePreview && (
                <div className="p-2 bg-slate-900 text-slate-100 rounded-lg font-mono text-[10px] leading-relaxed overflow-hidden border border-slate-800">
                    <code className="block whitespace-pre-wrap break-all">{codePreview}</code>
                </div>
            )}

            {/* Output Variables */}
            {hasOutputs && (
                <div>
                    <div className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">Outputs</div>
                    <div className="flex flex-wrap gap-1">
                        {nodeData.outputVariables!.slice(0, 4).map((v, i) => (
                            <span key={i} className="px-1.5 py-0.5 bg-emerald-50 rounded text-emerald-700 text-[10px] font-medium border border-emerald-100">
                                {v}
                            </span>
                        ))}
                        {nodeData.outputVariables!.length > 4 && (
                            <span className="px-1.5 py-0.5 text-muted-foreground text-[10px]">
                                +{nodeData.outputVariables!.length - 4}
                            </span>
                        )}
                    </div>
                </div>
            )}
        </BaseNode>
    );
}

export const CodeNode = memo(CodeNodeComponent);
