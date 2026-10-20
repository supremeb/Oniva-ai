'use client';

import { useState } from 'react';
import { Broadcast, ArrowClockwise } from '@phosphor-icons/react';
import { useQueryClient } from '@tanstack/react-query';
import { useFleetRuns } from '@/hooks/useFleetRuns';
import { RunStatusBadge } from '@/components/run/RunStatusBadge';
import { normalizeStatus } from '@/lib/run/status';
import type { FleetRunItem } from '@/lib/models';
import { cn } from '@/lib/utils';

type Tab = 'running' | 'attention' | 'completed' | 'all';

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

interface FleetRunListProps {
  selectedId: string | null;
  onSelect: (run: FleetRunItem) => void;
}

const TAB_STATUS: Record<Tab, string | undefined> = {
  running: 'running',
  attention: 'failed',
  completed: 'completed',
  all: undefined,
};

export function FleetRunList({ selectedId, onSelect }: FleetRunListProps) {
  const [tab, setTab] = useState<Tab>('running');
  const queryClient = useQueryClient();

  // Main list query
  const { data, isLoading: loading } = useFleetRuns({
    status: TAB_STATUS[tab],
    page: 1,
    page_size: 50,
  });

  // Badge count queries — lightweight, page_size=1
  const { data: runningData } = useFleetRuns({ status: 'running', page_size: 1 });
  const { data: attentionData } = useFleetRuns({ status: 'failed', page_size: 1 });

  const runningCount = runningData?.total ?? 0;
  const attentionCount = attentionData?.total ?? 0;

  const handleRefresh = () => {
    queryClient.invalidateQueries({ queryKey: ['fleet-runs'] });
  };

  const tabs: { key: Tab; label: string; badge?: number; badgeColor?: string }[] = [
    { key: 'running', label: 'Running', badge: runningCount || undefined },
    { key: 'attention', label: 'Attention', badge: attentionCount || undefined, badgeColor: 'bg-red-500' },
    { key: 'completed', label: 'Done' },
    { key: 'all', label: 'All' },
  ];

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Broadcast className="h-4 w-4 text-primary" weight="fill" />
          <span className="text-sm font-semibold">Fleet</span>
        </div>
        <button onClick={handleRefresh} className="p-1 rounded hover:bg-muted">
          <ArrowClockwise className="h-3.5 w-3.5 text-muted-foreground" />
        </button>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-border">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={cn(
              'flex-1 py-2 text-xs font-medium text-center transition-colors',
              tab === t.key
                ? 'text-primary border-b-2 border-primary'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            {t.label}
            {t.badge ? (
              <span className={cn(
                'ml-1 inline-flex items-center justify-center w-4 h-4 rounded-full text-white text-[10px]',
                t.badgeColor || 'bg-blue-500'
              )}>
                {t.badge}
              </span>
            ) : null}
          </button>
        ))}
      </div>

      {/* Run List */}
      <div className="flex-1 overflow-y-auto">
        {loading && !data ? (
          <div className="p-4 text-xs text-muted-foreground">Loading...</div>
        ) : data?.items.length === 0 ? (
          <div className="p-4 text-xs text-muted-foreground text-center">
            {tab === 'running' ? 'No agents running' : 'No runs yet'}
          </div>
        ) : (
          data?.items.map((run) => (
            <button
              key={run.id}
              data-testid={`fleet-run-${run.id}`}
              onClick={() => onSelect(run)}
              className={cn(
                'w-full text-left px-4 py-2.5 border-b border-border transition-colors',
                selectedId === run.id
                  ? 'bg-primary/5 border-l-2 border-l-primary'
                  : 'hover:bg-muted/50'
              )}
            >
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium truncate">
                  {run.agent_name || 'Unknown Agent'}
                </span>
                <RunStatusBadge status={normalizeStatus(run.status, 'fleet run')} className="ml-auto shrink-0" />
              </div>
              <div className="flex items-center gap-2 mt-0.5">
                <span className="text-[11px] text-muted-foreground">{run.trigger}</span>
                <span className="text-[11px] text-muted-foreground">—</span>
                <span className="text-[11px] text-muted-foreground">{timeAgo(run.created_at)}</span>
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  );
}
