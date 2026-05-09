'use client';

import dynamic from 'next/dynamic';
import { useBottomPanel, type BottomPanelTab } from '@/lib/stores/bottom-panel';
import { cn } from '@/lib/utils';

// Lazy load panel content for performance
const LogsPanel = dynamic(
    () => import('../LogsPanel').then((mod) => ({ default: mod.LogsPanel })),
    {
        loading: () => <PanelSkeleton />,
        ssr: false,
    }
);

const MonitoringPanel = dynamic(
    () => import('../MonitoringPanel').then((mod) => ({ default: mod.MonitoringPanel })),
    {
        loading: () => <PanelSkeleton />,
        ssr: false,
    }
);

const APIAccessPanel = dynamic(
    () => import('../APIAccessPanel').then((mod) => ({ default: mod.APIAccessPanel })),
    {
        loading: () => <PanelSkeleton />,
        ssr: false,
    }
);

function PanelSkeleton() {
    return (
        <div className="h-full p-4 space-y-3 animate-pulse">
            <div className="h-4 w-1/3 bg-zinc-800 rounded" />
            <div className="h-4 w-2/3 bg-zinc-800 rounded" />
            <div className="h-4 w-1/2 bg-zinc-800 rounded" />
        </div>
    );
}

interface BottomPanelContentProps {
    workflowId: string;
    className?: string;
}

export function BottomPanelContent({ workflowId, className }: BottomPanelContentProps) {
    const { activeTab } = useBottomPanel();

    return (
        <div className={cn('flex flex-col overflow-hidden', className)}>
            {/* Logs Panel */}
            <div
                id="panel-logs"
                role="tabpanel"
                aria-labelledby="tab-logs"
                hidden={activeTab !== 'logs'}
                className={cn('flex-1 min-h-0 overflow-hidden', activeTab !== 'logs' && 'hidden')}
            >
                {activeTab === 'logs' && <LogsPanel workflowId={workflowId} />}
            </div>

            {/* Monitoring Panel */}
            <div
                id="panel-monitoring"
                role="tabpanel"
                aria-labelledby="tab-monitoring"
                hidden={activeTab !== 'monitoring'}
                className={cn('flex-1 min-h-0 overflow-hidden', activeTab !== 'monitoring' && 'hidden')}
            >
                {activeTab === 'monitoring' && <MonitoringPanel workflowId={workflowId} />}
            </div>

            {/* API Panel */}
            <div
                id="panel-api"
                role="tabpanel"
                aria-labelledby="tab-api"
                hidden={activeTab !== 'api'}
                className={cn('flex-1 min-h-0 overflow-hidden', activeTab !== 'api' && 'hidden')}
            >
                {activeTab === 'api' && <APIAccessPanel workflowId={workflowId} />}
            </div>
        </div>
    );
}
