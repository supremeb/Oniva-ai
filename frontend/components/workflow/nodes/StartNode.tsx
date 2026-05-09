'use client';

import { memo } from 'react';
import { type NodeProps } from '@xyflow/react';
import { Play, TextT, Hash, File } from '@phosphor-icons/react';

import { BaseNode, NodeBadge } from './BaseNode';

export interface StartNodeVariable {
    id?: string;
    name: string;
    type: 'text' | 'number' | 'file' | 'string';
    required: boolean;
    defaultValue?: string;
}

export interface StartNodeData {
    label: string;
    variables: StartNodeVariable[];
}

const TypeIcon = ({ type }: { type: string }) => {
    const icons: Record<string, React.ReactNode> = {
        text: <TextT className="h-3 w-3" weight="bold" />,
        string: <TextT className="h-3 w-3" weight="bold" />,
        number: <Hash className="h-3 w-3" weight="bold" />,
        file: <File className="h-3 w-3" weight="bold" />,
    };
    return <>{icons[type] || icons.text}</>;
};

function StartNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as StartNodeData;
    const variables = nodeData.variables || [];

    return (
        <BaseNode
                id={id}
                selected={selected}
                nodeType="start"
                icon={<Play className="h-3.5 w-3.5 text-white" weight="fill" />}
                title="Start"
                category="Trigger"
                description="Workflow entry point"
                hasInput={false}
                hasOutput={true}
            >
            {variables.length === 0 ? (
                <div className="text-muted-foreground text-[11px] italic py-1">
                    No input variables defined
                </div>
            ) : (
                <div className="space-y-1.5">
                    {variables.slice(0, 4).map((v) => (
                        <div
                            key={v.id || v.name}
                            className="flex items-center gap-2 p-1.5 bg-slate-50 rounded-md border border-slate-100"
                        >
                            <div className="text-slate-500">
                                <TypeIcon type={v.type} />
                            </div>
                            <span className="font-medium text-[11px] flex-1 truncate">{v.name}</span>
                            <NodeBadge variant="default">{v.type}</NodeBadge>
                            {v.required && (
                                <span className="text-red-500 text-[10px] font-bold">*</span>
                            )}
                        </div>
                    ))}
                    {variables.length > 4 && (
                        <div className="text-[10px] text-muted-foreground text-center">
                            +{variables.length - 4} more variables
                        </div>
                    )}
                </div>
            )}
        </BaseNode>
    );
}

export const StartNode = memo(StartNodeComponent);
