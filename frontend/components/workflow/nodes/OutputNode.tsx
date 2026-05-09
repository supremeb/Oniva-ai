'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { Export, Browsers } from '@phosphor-icons/react';

import { BaseNode } from './BaseNode';

export interface OutputNodeData {
    label: string;
    outputVariables?: string[];
    outputName?: string;
    outputValue?: string;
    streamOutput?: boolean;
}

function OutputNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as OutputNodeData;
    const outputs = nodeData.outputVariables || [];

    return (
        <BaseNode
            id={id}
            selected={selected}
            nodeType="output"
            icon={<Export className="h-3.5 w-3.5 text-white" weight="fill" />}
            title="End"
            category="Output"
            description="Workflow exit point"
            hasInput={true}
            hasOutput={false}
        >
            {outputs.length === 0 && !nodeData.outputName ? (
                <div className="text-muted-foreground text-[11px] italic py-1">
                    No output variables configured
                </div>
            ) : (
                <div className="space-y-1.5">
                    {nodeData.outputName && (
                        <div className="flex items-center gap-2 p-1.5 bg-emerald-50 rounded-md border border-emerald-100">
                            <Browsers className="h-3 w-3 text-emerald-600" weight="fill" />
                            <span className="font-medium text-[11px] text-emerald-700">{nodeData.outputName}</span>
                        </div>
                    )}
                    {outputs.slice(0, 3).map((v, i) => (
                        <div key={i} className="flex items-center gap-2 p-1.5 bg-emerald-50 rounded-md border border-emerald-100">
                            <Browsers className="h-3 w-3 text-emerald-600" weight="fill" />
                            <span className="font-medium text-[11px] text-emerald-700 truncate">{v}</span>
                        </div>
                    ))}
                    {outputs.length > 3 && (
                        <div className="text-[10px] text-muted-foreground text-center">
                            +{outputs.length - 3} more outputs
                        </div>
                    )}
                </div>
            )}
        </BaseNode>
    );
}

export const OutputNode = memo(OutputNodeComponent);
