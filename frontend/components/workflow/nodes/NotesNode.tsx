'use client';

import { memo } from 'react';
import { type NodeProps, Handle, Position } from '@xyflow/react';
import { Note } from '@phosphor-icons/react';

export interface NotesNodeData {
    label: string;
    content: string;
    color: 'yellow' | 'blue' | 'green' | 'pink' | 'purple';
}

const colorClasses: Record<string, { bg: string; border: string; header: string }> = {
    yellow: {
        bg: 'bg-yellow-50',
        border: 'border-yellow-200',
        header: 'bg-yellow-100 text-yellow-800',
    },
    blue: {
        bg: 'bg-blue-50',
        border: 'border-blue-200',
        header: 'bg-blue-100 text-blue-800',
    },
    green: {
        bg: 'bg-green-50',
        border: 'border-green-200',
        header: 'bg-green-100 text-green-800',
    },
    pink: {
        bg: 'bg-pink-50',
        border: 'border-pink-200',
        header: 'bg-pink-100 text-pink-800',
    },
    purple: {
        bg: 'bg-purple-50',
        border: 'border-purple-200',
        header: 'bg-purple-100 text-purple-800',
    },
};

function NotesNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as NotesNodeData;
    const color = nodeData.color || 'yellow';
    const colors = colorClasses[color] || colorClasses.yellow;
    const content = nodeData.content || '';

    return (
        <div
            className={`min-w-[200px] max-w-[300px] rounded-lg border-2 shadow-sm transition-all duration-200 ${colors.bg} ${colors.border} ${selected ? 'ring-2 ring-primary ring-offset-2' : ''}`}
        >
            {/* Hidden handles for connection validation - notes don't actually connect */}
            <Handle
                type="target"
                position={Position.Top}
                className="!w-0 !h-0 !border-0 !bg-transparent !opacity-0"
                isConnectable={false}
            />

            {/* Header */}
            <div className={`px-3 py-2 rounded-t-lg flex items-center gap-2 ${colors.header}`}>
                <Note className="h-4 w-4" weight="duotone" />
                <span className="text-xs font-semibold">{nodeData.label || 'Note'}</span>
            </div>

            {/* Content */}
            <div className="px-3 py-2">
                <p className="text-xs text-gray-700 whitespace-pre-wrap line-clamp-6">
                    {content || 'Click to add notes...'}
                </p>
            </div>

            <Handle
                type="source"
                position={Position.Bottom}
                className="!w-0 !h-0 !border-0 !bg-transparent !opacity-0"
                isConnectable={false}
            />
        </div>
    );
}

export const NotesNode = memo(NotesNodeComponent);
