'use client';

import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { GitBranch, Warning } from '@phosphor-icons/react';
import { useWorkflowStore } from '@/lib/stores/workflow';
import { cn } from '@/lib/utils';
import { NodeInspector } from './NodeInspector';
import { NODE_THEMES } from './BaseNode';
import { canvasDotClass, canvasRingClass, canvasStatusLabel } from '@/lib/run/canvasStatus';

export interface IfElseCondition {
    id: string;
    variable: string;
    operator: 'equals' | 'not_equals' | 'contains' | 'greater_than' | 'less_than' | 'is_empty' | 'is_not_empty';
    value: string;
}

export interface IfElseNodeData {
    label: string;
    conditions: IfElseCondition[];
    logicalOperator: 'and' | 'or';
    status?: 'idle' | 'running' | 'complete' | 'error';
}

function IfElseNodeComponent({ id, data, selected }: NodeProps) {
    const nodeData = data as unknown as IfElseNodeData;
    const conditions = nodeData.conditions || [];
    const activeNodeId = useWorkflowStore((state) => state.activeNodeId);
    const nodeStatus = useWorkflowStore((state) => state.nodeStatusMap[id]);
    const isActive = activeNodeId === id;
    const hasError = conditions.length === 0;
    const theme = NODE_THEMES.ifelse;

    return (
        <NodeInspector id={id}>
            <div
                className={cn(
                    // Base card styling - white background with visible border
                    "rounded-xl border text-card-foreground",
                    theme.bg,
                    // Default state: light grey border, subtle shadow
                    "border-gray-200 shadow-sm",
                    // Smooth transitions for all states
                    "transition-all duration-200 ease-out",
                    // Hover state: darker border, lifted shadow
                    !selected && "hover:border-gray-300 hover:shadow-lg hover:-translate-y-0.5",
                    // Selected state: darker grey border + ring (no colored glow)
                    selected && "border-zinc-400 ring-1 ring-zinc-200 shadow-md -translate-y-0.5",
                    // Active state
                    isActive && "scale-[1.02] z-50",
                    // Status states
                    canvasRingClass(nodeStatus),
                    // Min width for consistency
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
                        <GitBranch className="h-3.5 w-3.5 text-white" weight="bold" />
                    </div>
                    <span className={cn("font-semibold text-sm flex-1 truncate", theme.accent)}>
                        {nodeData.label || 'IF/ELSE'}
                    </span>
                    {hasError && <Warning className="h-4 w-4 text-red-500 flex-shrink-0" weight="fill" />}
                </div>

                {/* Content */}
                <div className="p-3 text-xs space-y-2">
                    {conditions.length === 0 ? (
                        <div className="text-muted-foreground text-[11px] italic py-1">
                            No conditions defined
                        </div>
                    ) : (
                        <div className="space-y-1.5">
                            {conditions.slice(0, 3).map((c, i) => (
                                <div key={c.id || i} className="p-2 bg-yellow-50 rounded-lg border border-yellow-100 text-[10px]">
                                    <span className="font-semibold text-yellow-800">{c.variable || 'variable'}</span>{' '}
                                    <span className="text-yellow-600">{(c.operator || 'equals').replace(/_/g, ' ')}</span>{' '}
                                    <span className="text-muted-foreground font-mono">{c.value ?? "''"}</span>
                                    {i < Math.min(conditions.length, 3) - 1 && (
                                        <span className="ml-1.5 px-1 py-0.5 bg-yellow-200 text-yellow-700 rounded text-[9px] font-bold">
                                            {(nodeData.logicalOperator || 'and').toUpperCase()}
                                        </span>
                                    )}
                                </div>
                            ))}
                            {conditions.length > 3 && (
                                <div className="text-[10px] text-muted-foreground text-center">
                                    +{conditions.length - 3} more condition{conditions.length - 3 > 1 ? 's' : ''}
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
                        "!bg-yellow-400 hover:!bg-yellow-600"
                    )}
                />

                {/* True (IF) Handle - Green */}
                <Handle
                    type="source"
                    position={Position.Right}
                    id="true"
                    className={cn(
                        "!w-3 !h-3 !rounded-full !border-2 !border-white !shadow-sm",
                        "transition-all duration-150 hover:!w-4 hover:!h-4 hover:!shadow-md",
                        "!bg-emerald-500 hover:!bg-emerald-600"
                    )}
                    style={{ top: '35%' }}
                />
                {/* Floating "if true" label */}
                <span
                    className="absolute text-[9px] font-medium text-emerald-700 bg-emerald-50 border border-emerald-200 px-1.5 py-0.5 rounded whitespace-nowrap z-10"
                    style={{ top: '35%', right: -52, transform: 'translateY(-50%)' }}
                >
                    if true
                </span>

                {/* False (ELSE) Handle - Red */}
                <Handle
                    type="source"
                    position={Position.Right}
                    id="false"
                    className={cn(
                        "!w-3 !h-3 !rounded-full !border-2 !border-white !shadow-sm",
                        "transition-all duration-150 hover:!w-4 hover:!h-4 hover:!shadow-md",
                        "!bg-red-500 hover:!bg-red-600"
                    )}
                    style={{ top: '65%' }}
                />
                {/* Floating "if false" label */}
                <span
                    className="absolute text-[9px] font-medium text-red-700 bg-red-50 border border-red-200 px-1.5 py-0.5 rounded whitespace-nowrap z-10"
                    style={{ top: '65%', right: -56, transform: 'translateY(-50%)' }}
                >
                    if false
                </span>

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

export const IfElseNode = memo(IfElseNodeComponent);
