'use client';

import { TreeStructure, ClockCounterClockwise, ChartLine, Code } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';

export type WorkflowTabType = 'orchestrate' | 'logs' | 'monitor' | 'api';

interface WorkflowNavProps {
    activeTab: WorkflowTabType;
    onTabChange: (tab: WorkflowTabType) => void;
    nodeCount: number;
}

const tabs = [
    { id: 'orchestrate' as const, label: 'Orchestrate', icon: TreeStructure },
    { id: 'logs' as const, label: 'Logs', icon: ClockCounterClockwise },
    { id: 'monitor' as const, label: 'Monitoring', icon: ChartLine },
    { id: 'api' as const, label: 'API Access', icon: Code },
];

export function WorkflowNav({ activeTab, onTabChange, nodeCount }: WorkflowNavProps) {
    return (
        <aside className="w-14 h-full border-r bg-background flex flex-col items-center py-4 shrink-0">
            <div className="space-y-1">
                {tabs.map(({ id, label, icon: Icon }) => (
                    <button
                        key={id}
                        onClick={() => onTabChange(id)}
                        className={cn(
                            'w-10 h-10 rounded-lg flex items-center justify-center transition-colors relative group',
                            activeTab === id
                                ? 'bg-primary text-primary-foreground'
                                : 'hover:bg-muted text-muted-foreground'
                        )}
                        title={label}
                    >
                        <Icon className="h-5 w-5" weight={activeTab === id ? 'fill' : 'regular'} />
                        <span className="absolute left-12 px-2 py-1 bg-foreground text-background text-xs rounded opacity-0 group-hover:opacity-100 whitespace-nowrap z-50 pointer-events-none">
                            {label}
                        </span>
                    </button>
                ))}
            </div>

            <div className="mt-auto">
                <div className="w-10 h-10 rounded-lg bg-muted flex items-center justify-center text-xs font-medium">
                    {nodeCount}
                </div>
                <span className="text-[10px] text-muted-foreground mt-1 block text-center">Nodes</span>
            </div>
        </aside>
    );
}
