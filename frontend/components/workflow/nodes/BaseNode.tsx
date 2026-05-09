'use client';

import React, { memo } from 'react';
import { Handle, Position } from '@xyflow/react';
import { useWorkflowStore } from '@/lib/stores/workflow';
import { cn } from '@/lib/utils';
import { NodeInspector } from './NodeInspector';
import { Warning } from '@phosphor-icons/react';
import { Badge } from '@/components/ui/badge';

// Category definitions for nodes - used for visual badges
export type NodeCategory =
    | 'Trigger'      // start, webhook
    | 'Data'         // data retrieval, knowledge
    | 'AI'           // llm, agent, classifier
    | 'Logic'        // if/else, iteration, wait
    | 'Transform'    // template, code, aggregator, parameter extractor
    | 'Integration'  // connector, http
    | 'Output'       // output, notification
    | 'Utility';     // notes, variable assigner, list operator

// Category styling
export const CATEGORY_STYLES: Record<NodeCategory, { bg: string; text: string; border: string }> = {
    Trigger: { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-200' },
    Data: { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-200' },
    AI: { bg: 'bg-violet-50', text: 'text-violet-700', border: 'border-violet-200' },
    Logic: { bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-200' },
    Transform: { bg: 'bg-pink-50', text: 'text-pink-700', border: 'border-pink-200' },
    Integration: { bg: 'bg-teal-50', text: 'text-teal-700', border: 'border-teal-200' },
    Output: { bg: 'bg-emerald-50', text: 'text-emerald-700', border: 'border-emerald-200' },
    Utility: { bg: 'bg-slate-50', text: 'text-slate-700', border: 'border-slate-200' },
};

// Unified color palette for all node types
// All nodes have pure white body backgrounds with colored headers for visual hierarchy
export const NODE_THEMES = {
    start: {
        bg: 'bg-white',
        border: 'border-slate-300',
        header: 'bg-gradient-to-r from-slate-100 to-slate-50',
        icon: 'bg-slate-700',
        accent: 'text-slate-700',
        handle: 'bg-slate-500 hover:bg-slate-700',
        glow: 'rgba(100, 116, 139, 0.25)',
    },
    llm: {
        bg: 'bg-white',
        border: 'border-violet-300',
        header: 'bg-gradient-to-r from-violet-100 to-violet-50',
        icon: 'bg-violet-600',
        accent: 'text-violet-700',
        handle: 'bg-violet-500 hover:bg-violet-700',
        glow: 'rgba(139, 92, 246, 0.25)',
    },
    output: {
        bg: 'bg-white',
        border: 'border-emerald-300',
        header: 'bg-gradient-to-r from-emerald-100 to-emerald-50',
        icon: 'bg-emerald-600',
        accent: 'text-emerald-700',
        handle: 'bg-emerald-500 hover:bg-emerald-700',
        glow: 'rgba(16, 185, 129, 0.25)',
    },
    knowledge: {
        bg: 'bg-white',
        border: 'border-amber-300',
        header: 'bg-gradient-to-r from-amber-100 to-amber-50',
        icon: 'bg-amber-600',
        accent: 'text-amber-700',
        handle: 'bg-amber-500 hover:bg-amber-700',
        glow: 'rgba(245, 158, 11, 0.25)',
    },
    code: {
        bg: 'bg-white',
        border: 'border-sky-300',
        header: 'bg-gradient-to-r from-sky-100 to-sky-50',
        icon: 'bg-sky-600',
        accent: 'text-sky-700',
        handle: 'bg-sky-500 hover:bg-sky-700',
        glow: 'rgba(14, 165, 233, 0.25)',
    },
    http: {
        bg: 'bg-white',
        border: 'border-orange-300',
        header: 'bg-gradient-to-r from-orange-100 to-orange-50',
        icon: 'bg-orange-600',
        accent: 'text-orange-700',
        handle: 'bg-orange-500 hover:bg-orange-700',
        glow: 'rgba(249, 115, 22, 0.25)',
    },
    ifelse: {
        bg: 'bg-white',
        border: 'border-yellow-300',
        header: 'bg-gradient-to-r from-yellow-100 to-yellow-50',
        icon: 'bg-yellow-600',
        accent: 'text-yellow-700',
        handle: 'bg-yellow-500 hover:bg-yellow-700',
        glow: 'rgba(234, 179, 8, 0.25)',
    },
    iteration: {
        bg: 'bg-white',
        border: 'border-cyan-300',
        header: 'bg-gradient-to-r from-cyan-100 to-cyan-50',
        icon: 'bg-cyan-600',
        accent: 'text-cyan-700',
        handle: 'bg-cyan-500 hover:bg-cyan-700',
        glow: 'rgba(6, 182, 212, 0.25)',
    },
    template: {
        bg: 'bg-white',
        border: 'border-pink-300',
        header: 'bg-gradient-to-r from-pink-100 to-pink-50',
        icon: 'bg-pink-600',
        accent: 'text-pink-700',
        handle: 'bg-pink-500 hover:bg-pink-700',
        glow: 'rgba(236, 72, 153, 0.25)',
    },
    aggregator: {
        bg: 'bg-white',
        border: 'border-teal-300',
        header: 'bg-gradient-to-r from-teal-100 to-teal-50',
        icon: 'bg-teal-600',
        accent: 'text-teal-700',
        handle: 'bg-teal-500 hover:bg-teal-700',
        glow: 'rgba(20, 184, 166, 0.25)',
    },
    classifier: {
        bg: 'bg-white',
        border: 'border-fuchsia-300',
        header: 'bg-gradient-to-r from-fuchsia-100 to-fuchsia-50',
        icon: 'bg-fuchsia-600',
        accent: 'text-fuchsia-700',
        handle: 'bg-fuchsia-500 hover:bg-fuchsia-700',
        glow: 'rgba(217, 70, 239, 0.25)',
    },
    skill: {
        bg: 'bg-white',
        border: 'border-green-300',
        header: 'bg-gradient-to-r from-green-100 to-green-50',
        icon: 'bg-green-600',
        accent: 'text-green-700',
        handle: 'bg-green-500 hover:bg-green-700',
        glow: 'rgba(34, 197, 94, 0.25)',
    },
    connector: {
        bg: 'bg-white',
        border: 'border-teal-300',
        header: 'bg-gradient-to-r from-teal-100 to-teal-50',
        icon: 'bg-teal-600',
        accent: 'text-teal-700',
        handle: 'bg-teal-500 hover:bg-teal-700',
        glow: 'rgba(20, 184, 166, 0.25)',
    },
    agent: {
        bg: 'bg-white',
        border: 'border-indigo-300',
        header: 'bg-gradient-to-r from-indigo-100 to-indigo-50',
        icon: 'bg-indigo-600',
        accent: 'text-indigo-700',
        handle: 'bg-indigo-500 hover:bg-indigo-700',
        glow: 'rgba(99, 102, 241, 0.25)',
    },
    data: {
        bg: 'bg-white',
        border: 'border-blue-300',
        header: 'bg-gradient-to-r from-blue-100 to-blue-50',
        icon: 'bg-blue-600',
        accent: 'text-blue-700',
        handle: 'bg-blue-500 hover:bg-blue-700',
        glow: 'rgba(59, 130, 246, 0.25)',
    },
    analysis: {
        bg: 'bg-white',
        border: 'border-lime-300',
        header: 'bg-gradient-to-r from-lime-100 to-lime-50',
        icon: 'bg-lime-600',
        accent: 'text-lime-700',
        handle: 'bg-lime-500 hover:bg-lime-700',
        glow: 'rgba(132, 204, 22, 0.25)',
    },
    compliance: {
        bg: 'bg-white',
        border: 'border-purple-300',
        header: 'bg-gradient-to-r from-purple-100 to-purple-50',
        icon: 'bg-purple-600',
        accent: 'text-purple-700',
        handle: 'bg-purple-500 hover:bg-purple-700',
        glow: 'rgba(168, 85, 247, 0.25)',
    },
    research: {
        bg: 'bg-white',
        border: 'border-rose-300',
        header: 'bg-gradient-to-r from-rose-100 to-rose-50',
        icon: 'bg-rose-600',
        accent: 'text-rose-700',
        handle: 'bg-rose-500 hover:bg-rose-700',
        glow: 'rgba(244, 63, 94, 0.25)',
    },
    // Phase 3: Essential workflow nodes
    wait: {
        bg: 'bg-white',
        border: 'border-indigo-300',
        header: 'bg-gradient-to-r from-indigo-100 to-indigo-50',
        icon: 'bg-indigo-600',
        accent: 'text-indigo-700',
        handle: 'bg-indigo-500 hover:bg-indigo-700',
        glow: 'rgba(99, 102, 241, 0.25)',
    },
    variable_assigner: {
        bg: 'bg-white',
        border: 'border-cyan-300',
        header: 'bg-gradient-to-r from-cyan-100 to-cyan-50',
        icon: 'bg-cyan-600',
        accent: 'text-cyan-700',
        handle: 'bg-cyan-500 hover:bg-cyan-700',
        glow: 'rgba(6, 182, 212, 0.25)',
    },
    list_operator: {
        bg: 'bg-white',
        border: 'border-cyan-300',
        header: 'bg-gradient-to-r from-cyan-100 to-cyan-50',
        icon: 'bg-cyan-600',
        accent: 'text-cyan-700',
        handle: 'bg-cyan-500 hover:bg-cyan-700',
        glow: 'rgba(6, 182, 212, 0.25)',
    },
    notes: {
        bg: 'bg-white',
        border: 'border-yellow-300',
        header: 'bg-gradient-to-r from-yellow-100 to-yellow-50',
        icon: 'bg-yellow-600',
        accent: 'text-yellow-700',
        handle: 'bg-yellow-500 hover:bg-yellow-700',
        glow: 'rgba(234, 179, 8, 0.25)',
    },
    document_extractor: {
        bg: 'bg-white',
        border: 'border-green-300',
        header: 'bg-gradient-to-r from-green-100 to-green-50',
        icon: 'bg-green-600',
        accent: 'text-green-700',
        handle: 'bg-green-500 hover:bg-green-700',
        glow: 'rgba(34, 197, 94, 0.25)',
    },
    parameter_extractor: {
        bg: 'bg-white',
        border: 'border-violet-300',
        header: 'bg-gradient-to-r from-violet-100 to-violet-50',
        icon: 'bg-violet-600',
        accent: 'text-violet-700',
        handle: 'bg-violet-500 hover:bg-violet-700',
        glow: 'rgba(139, 92, 246, 0.25)',
    },
    ocr: {
        bg: 'bg-white',
        border: 'border-sky-300',
        header: 'bg-gradient-to-r from-sky-100 to-sky-50',
        icon: 'bg-sky-600',
        accent: 'text-sky-700',
        handle: 'bg-sky-500 hover:bg-sky-700',
        glow: 'rgba(14, 165, 233, 0.25)',
    },
    notification: {
        bg: 'bg-white',
        border: 'border-orange-300',
        header: 'bg-gradient-to-r from-orange-100 to-orange-50',
        icon: 'bg-orange-600',
        accent: 'text-orange-700',
        handle: 'bg-orange-500 hover:bg-orange-700',
        glow: 'rgba(249, 115, 22, 0.25)',
    },
    webhook_trigger: {
        bg: 'bg-white',
        border: 'border-orange-300',
        header: 'bg-gradient-to-r from-orange-100 to-orange-50',
        icon: 'bg-orange-600',
        accent: 'text-orange-700',
        handle: 'bg-orange-500 hover:bg-orange-700',
        glow: 'rgba(249, 115, 22, 0.25)',
    },
    subworkflow: {
        bg: 'bg-white',
        border: 'border-indigo-300',
        header: 'bg-gradient-to-r from-indigo-100 to-indigo-50',
        icon: 'bg-indigo-600',
        accent: 'text-indigo-700',
        handle: 'bg-indigo-500 hover:bg-indigo-700',
        glow: 'rgba(99, 102, 241, 0.25)',
    },
    human_in_loop: {
        bg: 'bg-white',
        border: 'border-red-300',
        header: 'bg-gradient-to-r from-red-100 to-red-50',
        icon: 'bg-red-600',
        accent: 'text-red-700',
        handle: 'bg-red-500 hover:bg-red-700',
        glow: 'rgba(239, 68, 68, 0.25)',
    },
    // Phase 2: Workflow scheduling nodes
    schedule_trigger: {
        bg: 'bg-white',
        border: 'border-blue-300',
        header: 'bg-gradient-to-r from-blue-100 to-blue-50',
        icon: 'bg-blue-600',
        accent: 'text-blue-700',
        handle: 'bg-blue-500 hover:bg-blue-700',
        glow: 'rgba(59, 130, 246, 0.25)',
    },
    // Phase 3: Spreadsheet processing nodes
    spreadsheet: {
        bg: 'bg-white',
        border: 'border-emerald-300',
        header: 'bg-gradient-to-r from-emerald-100 to-emerald-50',
        icon: 'bg-emerald-600',
        accent: 'text-emerald-700',
        handle: 'bg-emerald-500 hover:bg-emerald-700',
        glow: 'rgba(16, 185, 129, 0.25)',
    },
} as const;

export type NodeTheme = keyof typeof NODE_THEMES;

interface BaseNodeProps {
    id: string;
    selected?: boolean;
    nodeType: NodeTheme;
    icon: React.ReactNode;
    title: string;
    /** Optional category badge displayed in header (e.g., "Data", "AI", "Logic") */
    category?: NodeCategory;
    /** Optional description subtitle shown below title */
    description?: string;
    hasError?: boolean;
    hasInput?: boolean;
    hasOutput?: boolean;
    multipleOutputs?: { id: string; label: string; position?: number }[];
    children?: React.ReactNode;
    className?: string;
}

// Improved handle component with refined sizing - smaller default, grows on hover
const NodeHandle = memo(({
    type,
    position,
    id,
    handleColor,
    label,
    style,
}: {
    type: 'source' | 'target';
    position: Position;
    id?: string;
    handleColor: string;
    label?: string;
    style?: React.CSSProperties;
}) => {
    return (
        <Handle
            type={type}
            position={position}
            id={id}
            className={cn(
                // Smaller default size (3x3) - grows on hover (4x4)
                "!w-3 !h-3 !rounded-full !border-2 !border-white",
                // Subtle shadow
                "!shadow-sm",
                // Smooth transition for hover and connection states
                "transition-all duration-150 ease-out",
                // Grow on hover
                "hover:!w-4 hover:!h-4 hover:!shadow-md",
                // Active connection state - pulse effect
                "active:!scale-125",
                handleColor
            )}
            style={style}
        />
    );
});

NodeHandle.displayName = 'NodeHandle';

export const BaseNode = memo(({
    id,
    selected,
    nodeType,
    icon,
    title,
    category,
    description,
    hasError = false,
    hasInput = true,
    hasOutput = true,
    multipleOutputs,
    children,
    className,
}: BaseNodeProps) => {
    const activeNodeId = useWorkflowStore((state) => state.activeNodeId);
    const nodeStatus = useWorkflowStore((state) => state.nodeStatusMap[id] || 'pending');

    const theme = NODE_THEMES[nodeType];
    const isActive = activeNodeId === id;
    const categoryStyle = category ? CATEGORY_STYLES[category] : null;

    const statusClasses = {
        pending: '',
        running: 'ring-2 ring-blue-500 ring-offset-2 shadow-[0_0_20px_rgba(59,130,246,0.4)] animate-pulse',
        complete: 'ring-2 ring-green-500 ring-offset-1 shadow-[0_0_12px_rgba(34,197,94,0.3)]',
        error: 'ring-2 ring-red-500 ring-offset-1 shadow-[0_0_12px_rgba(239,68,68,0.3)]',
    };

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
                    statusClasses[nodeStatus],
                    // Min width for consistency
                    "min-w-[240px] max-w-[320px]",
                    className
                )}
            >
                {/* Header with icon, title/description, and optional category badge */}
                <div className={cn(
                    "flex items-center justify-between px-3 py-2.5 rounded-t-[10px] border-b",
                    theme.header,
                    theme.border
                )}>
                    <div className="flex items-center gap-2 flex-1 min-w-0">
                        <div className={cn(
                            "w-7 h-7 rounded-lg flex items-center justify-center shadow-sm flex-shrink-0",
                            theme.icon
                        )}>
                            {icon}
                        </div>
                        <div className="flex flex-col min-w-0 gap-0.5">
                            <span className={cn(
                                "font-semibold text-[13px] leading-tight truncate",
                                theme.accent
                            )}>
                                {title}
                            </span>
                            {description && (
                                <span className="text-[10px] text-muted-foreground/80 truncate max-w-[150px] leading-tight">
                                    {description}
                                </span>
                            )}
                        </div>
                    </div>
                    <div className="flex items-center gap-1 flex-shrink-0">
                        {category && categoryStyle && (
                            <Badge
                                variant="outline"
                                className={cn(
                                    "text-[8px] px-1.5 py-0 h-4 font-semibold border uppercase tracking-wider",
                                    categoryStyle.bg,
                                    categoryStyle.text,
                                    categoryStyle.border
                                )}
                            >
                                {category}
                            </Badge>
                        )}
                        {hasError && (
                            <Warning className="h-3.5 w-3.5 text-red-500 flex-shrink-0" weight="fill" />
                        )}
                    </div>
                </div>

                {/* Content - clean white area */}
                {children && (
                    <div className="px-3 py-2.5 text-xs space-y-1.5">
                        {children}
                    </div>
                )}

                {/* Input Handle */}
                {hasInput && (
                    <NodeHandle
                        type="target"
                        position={Position.Left}
                        handleColor={cn("!", theme.handle)}
                    />
                )}

                {/* Output Handle(s) */}
                {hasOutput && !multipleOutputs && (
                    <NodeHandle
                        type="source"
                        position={Position.Right}
                        handleColor={cn("!", theme.handle)}
                    />
                )}

                {/* Multiple Output Handles (for if/else, classifier, etc.) */}
                {multipleOutputs && multipleOutputs.map((output, index) => {
                    // Calculate vertical position for multiple handles
                    const total = multipleOutputs.length;
                    const spacing = 100 / (total + 1);
                    const topPercent = output.position ?? (spacing * (index + 1));

                    return (
                        <NodeHandle
                            key={output.id}
                            type="source"
                            position={Position.Right}
                            id={output.id}
                            handleColor={cn("!", theme.handle)}
                            style={{ top: `${topPercent}%` }}
                        />
                    );
                })}

                {/* Status Indicator */}
                {nodeStatus !== 'pending' && (
                    <div className={cn(
                        "absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full",
                        "border-2 border-white shadow-sm z-50",
                        nodeStatus === 'running' && "bg-blue-500 animate-pulse",
                        nodeStatus === 'complete' && "bg-green-500",
                        nodeStatus === 'error' && "bg-red-500"
                    )} />
                )}
            </div>
        </NodeInspector>
    );
});

BaseNode.displayName = 'BaseNode';

// Helper component for displaying key-value info in nodes - compact design
export const NodeInfo = memo(({ label, value, valueClass }: {
    label: string;
    value: React.ReactNode;
    valueClass?: string;
}) => (
    <div className="flex items-center justify-between gap-2 py-0.5">
        <span className="text-muted-foreground/70 text-[10px] uppercase tracking-wider font-medium">{label}</span>
        <span className={cn("font-medium text-[11px] truncate max-w-[130px]", valueClass)}>
            {value}
        </span>
    </div>
));

NodeInfo.displayName = 'NodeInfo';

// Helper component for prompt previews - clean subtle background
export const NodePromptPreview = memo(({ label, content, bgClass }: {
    label: string;
    content: string;
    bgClass?: string;
}) => (
    <div className={cn("px-2 py-1.5 rounded-md border border-slate-100", bgClass || "bg-slate-50/50")}>
        <div className="text-[9px] font-semibold text-muted-foreground/60 uppercase tracking-wider mb-0.5">{label}</div>
        <div className="text-[10px] line-clamp-2 leading-snug text-slate-600">{content}</div>
    </div>
));

NodePromptPreview.displayName = 'NodePromptPreview';

// Helper component for tags/badges in nodes - refined styling
export const NodeBadge = memo(({ children, variant = 'default' }: {
    children: React.ReactNode;
    variant?: 'default' | 'success' | 'warning' | 'error' | 'info';
}) => {
    const variants = {
        default: 'bg-slate-100 text-slate-600 border-slate-200',
        success: 'bg-green-50 text-green-700 border-green-200',
        warning: 'bg-amber-50 text-amber-700 border-amber-200',
        error: 'bg-red-50 text-red-700 border-red-200',
        info: 'bg-blue-50 text-blue-700 border-blue-200',
    };

    return (
        <span className={cn(
            "inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md border text-[9px] font-medium",
            variants[variant]
        )}>
            {children}
        </span>
    );
});

NodeBadge.displayName = 'NodeBadge';
