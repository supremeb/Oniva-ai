'use client';

import React, { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { ChatTeardropText, Warning } from '@phosphor-icons/react';
import { useWorkflowStore } from '@/lib/stores/workflow';
import { cn } from '@/lib/utils';
import { NodeInspector } from './NodeInspector';
import { NODE_THEMES } from './BaseNode';
import { canvasDotClass, canvasRingClass, canvasStatusLabel } from '@/lib/run/canvasStatus';

export interface QuestionClassifierClass {
    id: string;
    name: string;
    description?: string;
}

export interface QuestionClassifierNodeData {
    label: string;
    model?: string;
    classes?: QuestionClassifierClass[];
    categories?: QuestionClassifierClass[]; // Legacy property name (backward compatibility)
    inputVariable?: string;
    input?: string; // Legacy property name (backward compatibility)
    status?: 'idle' | 'running' | 'complete' | 'error';
}

// Color palette for classification branches with label colors
const CLASS_COLORS = [
    { bg: 'bg-emerald-500', hover: 'hover:bg-emerald-600', dot: 'bg-emerald-500', text: 'text-emerald-700', labelBg: 'bg-emerald-50', labelBorder: 'border-emerald-200' },
    { bg: 'bg-blue-500', hover: 'hover:bg-blue-600', dot: 'bg-blue-500', text: 'text-blue-700', labelBg: 'bg-blue-50', labelBorder: 'border-blue-200' },
    { bg: 'bg-amber-500', hover: 'hover:bg-amber-600', dot: 'bg-amber-500', text: 'text-amber-700', labelBg: 'bg-amber-50', labelBorder: 'border-amber-200' },
    { bg: 'bg-rose-500', hover: 'hover:bg-rose-600', dot: 'bg-rose-500', text: 'text-rose-700', labelBg: 'bg-rose-50', labelBorder: 'border-rose-200' },
    { bg: 'bg-purple-500', hover: 'hover:bg-purple-600', dot: 'bg-purple-500', text: 'text-purple-700', labelBg: 'bg-purple-50', labelBorder: 'border-purple-200' },
];

function QuestionClassifierNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as QuestionClassifierNodeData;
    // Support both 'classes' (new) and 'categories' (legacy) property names
    const classes = nodeData.classes || nodeData.categories || [];
    const activeNodeId = useWorkflowStore((state) => state.activeNodeId);
    const nodeStatus = useWorkflowStore((state) => state.nodeStatusMap[id]);
    const isActive = activeNodeId === id;
    const hasError = classes.length === 0;
    const theme = NODE_THEMES.classifier;

    // Calculate handle positions based on number of classes
    const visibleClasses = classes.slice(0, 5);
    const handleSpacing = 100 / (visibleClasses.length + 1);

    return (
        <NodeInspector id={id}>
            <div
                className={cn(
                    "rounded-xl border-2 bg-card text-card-foreground",
                    "shadow-sm hover:shadow-md",
                    "transition-all duration-200 ease-out",
                    theme.border,
                    selected && "ring-2 ring-primary ring-offset-2 shadow-lg",
                    isActive && "scale-[1.02] z-50",
                    canvasRingClass(nodeStatus),
                    "min-w-[220px] max-w-[280px]"
                )}
            >
                {/* Header */}
                <div className={cn(
                    "flex items-center gap-2.5 p-3 rounded-t-[10px] border-b",
                    theme.header,
                    theme.border
                )}>
                    <div className={cn("w-7 h-7 rounded-lg flex items-center justify-center shadow-sm", theme.icon)}>
                        <ChatTeardropText className="h-3.5 w-3.5 text-white" weight="fill" />
                    </div>
                    <span className={cn("font-semibold text-sm flex-1 truncate", theme.accent)}>
                        {nodeData.label || 'Question Classifier'}
                    </span>
                    {hasError && <Warning className="h-4 w-4 text-red-500 flex-shrink-0" weight="fill" />}
                </div>

                {/* Content */}
                <div className="p-3 text-xs space-y-2">
                    {classes.length === 0 ? (
                        <div className="text-muted-foreground text-[11px] italic">
                            No classes defined
                        </div>
                    ) : (
                        <div className="space-y-1.5">
                            {visibleClasses.map((c, i) => {
                                const colorScheme = CLASS_COLORS[i % CLASS_COLORS.length];
                                return (
                                    <div
                                        key={c.id || `class-${i}`}
                                        className="p-2 bg-fuchsia-50/70 rounded-lg border border-fuchsia-100 flex items-center gap-2"
                                    >
                                        <div className={cn(
                                            "w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold text-white",
                                            colorScheme.bg
                                        )}>
                                            {i + 1}
                                        </div>
                                        <span className="font-medium text-[11px] flex-1 truncate">
                                            {c.name || 'Unnamed class'}
                                        </span>
                                        <div className={cn("w-2.5 h-2.5 rounded-full", colorScheme.dot)} />
                                    </div>
                                );
                            })}
                            {classes.length > 5 && (
                                <div className="text-[10px] text-muted-foreground text-center pt-1">
                                    +{classes.length - 5} more class{classes.length - 5 > 1 ? 'es' : ''}
                                </div>
                            )}
                        </div>
                    )}
                </div>

                {/* Input Handle */}
                <Handle
                    type="target"
                    position={Position.Left}
                    className={cn(
                        "!w-3 !h-3 !rounded-full !border-2 !border-white !shadow-sm",
                        "transition-all duration-150 hover:!w-4 hover:!h-4 hover:!shadow-md",
                        "!bg-fuchsia-400 hover:!bg-fuchsia-600"
                    )}
                />

                {/* Multiple output handles for each class - color coded with floating labels */}
                {visibleClasses.map((c, i) => {
                    const colorScheme = CLASS_COLORS[i % CLASS_COLORS.length];
                    const topPosition = handleSpacing * (i + 1);
                    return (
                        <React.Fragment key={c.id || `handle-${i}`}>
                            <Handle
                                type="source"
                                position={Position.Right}
                                id={c.id || `class-${i}`}
                                className={cn(
                                    "!w-3 !h-3 !rounded-full !border-2 !border-white !shadow-sm",
                                    "transition-all duration-150 hover:!w-4 hover:!h-4 hover:!shadow-md",
                                    `!${colorScheme.bg}`,
                                    colorScheme.hover
                                )}
                                style={{ top: `${topPosition}%` }}
                            />
                            {/* Floating class label */}
                            <span
                                className={cn(
                                    "absolute text-[8px] font-medium px-1.5 py-0.5 rounded whitespace-nowrap border z-10",
                                    colorScheme.text,
                                    colorScheme.labelBg,
                                    colorScheme.labelBorder
                                )}
                                style={{ top: `${topPosition}%`, right: -60, transform: 'translateY(-50%)' }}
                            >
                                {c.name ? (c.name.length > 10 ? c.name.slice(0, 10) + '…' : c.name) : `Class ${i + 1}`}
                            </span>
                        </React.Fragment>
                    );
                })}

                {/* Status Indicator */}
                {canvasDotClass(nodeStatus) && (
                    <div
                        data-testid="node-status-dot"
                        data-status={nodeStatus}
                        title={canvasStatusLabel(nodeStatus) ?? undefined}
                        className={cn("absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full",
                        "border-2 border-white shadow-sm z-50", canvasDotClass(nodeStatus))}
                    />
                )}
            </div>
        </NodeInspector>
    );
}

export const QuestionClassifierNode = memo(QuestionClassifierNodeComponent);
