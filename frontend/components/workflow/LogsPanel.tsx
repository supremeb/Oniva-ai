'use client';

import React, { useState, useCallback, useMemo } from 'react';
// Card components removed from main panel - using divs for clean borderless design
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import {
  MagnifyingGlass,
  XCircle,
  Clock,
  CaretRight,
  Lightning,
  Code,
  Calendar,
  ArrowClockwise,
} from '@phosphor-icons/react';
import { useWorkflowLogs, useLogStream } from '@/hooks/useWorkflowLogs';
import { useWorkflowVersions } from '@/hooks/useWorkflowVersions';
import { LogDetailView } from './LogDetailView';
import { RunStatusBadge } from '@/components/run/RunStatusBadge';
import { isActive, normalizeStatus, runStatusLabel } from '@/lib/run/status';
import type {
  WorkflowLogSummary,
  LogFilterParams,
  TimeRange,
} from '@/lib/api/workflow-logs';

interface LogsPanelProps {
  workflowId: string;
}

type ViewMode = 'list' | 'detail';
type StatusFilter = 'all' | 'success' | 'failed' | 'running';

const triggerConfig = {
  manual: { icon: Lightning, label: 'Manual' },
  api: { icon: Code, label: 'API' },
  scheduled: { icon: Calendar, label: 'Scheduled' },
};

function formatDuration(ms: number | null): string {
  if (ms === null) return '-';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
}

function formatTime(isoString: string | null): string {
  if (!isoString) return 'Pending...';
  return new Date(isoString).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function LogsPanel({ workflowId }: LogsPanelProps) {
  // View state
  const [viewMode, setViewMode] = useState<ViewMode>('list');
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  // Filter state
  const [searchInput, setSearchInput] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [timeFilter, setTimeFilter] = useState<TimeRange>('7d');
  const [versionFilter, setVersionFilter] = useState<number | 'all'>('all');

  // Build filter params
  const filterParams = useMemo<LogFilterParams>(() => ({
    status: statusFilter === 'all' ? undefined : statusFilter,
    time_range: timeFilter,
    search: searchInput || undefined,
    version: versionFilter === 'all' ? undefined : versionFilter,
    limit: 20,
  }), [statusFilter, timeFilter, searchInput, versionFilter]);

  // Data fetching
  const { data, isLoading, error, refetch, isFetching } = useWorkflowLogs(workflowId, filterParams);

  // Fetch workflow versions for dynamic version filter
  // Include drafts since they can be executed during testing
  const { data: versionsData } = useWorkflowVersions(workflowId, {
    includeDrafts: true,
    limit: 50,
  });

  // SSE connection for real-time updates
  const { isConnected } = useLogStream(workflowId);

  // Handlers
  const handleViewDetail = useCallback((runId: string) => {
    setSelectedRunId(runId);
    setViewMode('detail');
  }, []);

  const handleBack = useCallback(() => {
    setViewMode('list');
    setSelectedRunId(null);
  }, []);

  const handleSearch = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      refetch();
    }
  }, [refetch]);

  // Render detail view
  if (viewMode === 'detail' && selectedRunId) {
    return (
      <LogDetailView
        workflowId={workflowId}
        runId={selectedRunId}
        onBack={handleBack}
      />
    );
  }

  // Render list view
  return (
    <div className="h-full flex flex-col bg-white dark:bg-zinc-950">
      <div className="px-4 py-2 border-b border-zinc-200 dark:border-zinc-800">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-base font-semibold">Workflow Logs</h3>
            <p className="text-sm text-muted-foreground">
              Execution history for this workflow
            </p>
          </div>
          <div className="flex items-center gap-2">
            {isConnected && (
              <span className="flex items-center gap-1 text-xs text-green-600">
                <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                Live
              </span>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => refetch()}
              disabled={isFetching}
            >
              <ArrowClockwise className={`h-4 w-4 ${isFetching ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        </div>
      </div>

      <div className="p-4 border-b border-zinc-200 dark:border-zinc-800 space-y-3">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <MagnifyingGlass className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search logs..."
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={handleSearch}
              className="pl-8"
            />
          </div>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            className="h-9 rounded-md border px-3 text-sm bg-background"
            aria-label="Filter logs by status"
          >
            <option value="all">All Status</option>
            <option value="success">Success</option>
            <option value="failed">Failed</option>
            <option value="running">Running</option>
          </select>
          <select
            value={timeFilter}
            onChange={(e) => setTimeFilter(e.target.value as TimeRange)}
            className="h-9 rounded-md border px-3 text-sm bg-background"
            aria-label="Filter logs by time range"
          >
            <option value="7d">Last 7 Days</option>
            <option value="24h">Last 24 Hours</option>
            <option value="1h">Last Hour</option>
            <option value="30d">Last 30 Days</option>
          </select>
          <select
            value={versionFilter}
            onChange={(e) => setVersionFilter(e.target.value === 'all' ? 'all' : parseInt(e.target.value))}
            className="h-9 rounded-md border px-3 text-sm bg-background"
            aria-label="Filter logs by version"
          >
            <option value="all">All Versions</option>
            {/* Dynamic version options from API */}
            {versionsData?.map((version) => (
              <option key={version.id} value={version.version_number}>
                v{version.version_tag || version.version_number}
                {version.is_active ? ' (Live)' : version.is_draft ? ' (Draft)' : ''}
              </option>
            ))}
          </select>
        </div>

        {data && (
          <div className="text-xs text-muted-foreground">
            Showing {data.logs.length} logs {data.has_more && '(more available)'}
          </div>
        )}
      </div>

      <ScrollArea className="flex-1">
        {isLoading ? (
          <LogListSkeleton />
        ) : error ? (
          <div className="p-8 text-center text-muted-foreground">
            <XCircle className="h-12 w-12 mx-auto mb-4 text-red-400" />
            <p className="text-sm">Failed to load logs</p>
            <Button variant="outline" size="sm" className="mt-4" onClick={() => refetch()}>
              Retry
            </Button>
          </div>
        ) : !data?.logs.length ? (
          <div className="p-8 text-center text-muted-foreground">
            <Clock className="h-12 w-12 mx-auto mb-4 opacity-50" />
            <p className="text-sm">No logs yet</p>
            <p className="text-xs mt-1">Run the workflow to generate logs</p>
          </div>
        ) : (
          <div className="divide-y">
            {data.logs.map((log) => (
              <LogListItem
                key={log.id}
                log={log}
                onClick={() => handleViewDetail(log.id)}
              />
            ))}

            {data.has_more && (
              <div className="p-4 text-center">
                <Button variant="outline" size="sm" onClick={() => refetch()}>
                  Load more
                </Button>
              </div>
            )}
          </div>
        )}
      </ScrollArea>
    </div>
  );
}

// ============================================
// Sub-components
// ============================================

interface LogListItemProps {
  log: WorkflowLogSummary;
  onClick: () => void;
}

const LogListItem = React.memo(function LogListItem({ log, onClick }: LogListItemProps) {
  const status = normalizeStatus(log.status, 'workflow_log');
  const trigger = triggerConfig[log.trigger] || triggerConfig.manual;
  const TriggerIcon = trigger.icon;

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onClick();
    }
  }, [onClick]);

  return (
    <div
      role="button"
      tabIndex={0}
      data-testid={`log-row-${log.id}`}
      className="p-4 hover:bg-muted/50 cursor-pointer flex items-center gap-4 transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
      onClick={onClick}
      onKeyDown={handleKeyDown}
      aria-label={`View details for ${runStatusLabel(status).toLowerCase()} log from ${formatTime(log.started_at)}`}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium">{formatTime(log.started_at)}</span>
          <RunStatusBadge status={status} testId="log-status" />
          {log.version_number && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-violet-50 text-violet-600 dark:bg-violet-950 dark:text-violet-400">
              v{log.version_number}
            </span>
          )}
          <span className="text-xs text-muted-foreground flex items-center gap-1">
            <TriggerIcon className="h-3 w-3" aria-hidden="true" />
            {trigger.label}
          </span>
        </div>
        {log.error && (
          <p className="text-xs text-red-500 mt-1 truncate">{log.error}</p>
        )}
      </div>

      <div className="text-right text-xs text-muted-foreground space-y-0.5">
        <div>{formatDuration(log.duration_ms)}</div>
        {log.total_tokens > 0 && <div>{log.total_tokens.toLocaleString()} tokens</div>}
        {log.nodes_executed > 0 && <div>{log.nodes_executed} nodes</div>}
      </div>

      {isActive(status) && (
        <div className="text-sm font-medium text-blue-500">
          {log.progress}%
        </div>
      )}

      <CaretRight className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
    </div>
  );
});

function LogListSkeleton() {
  return (
    <div className="divide-y">
      {[...Array(5)].map((_, i) => (
        <div key={i} className="p-4 flex items-center gap-4">
          <Skeleton className="h-8 w-8 rounded-full" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-3 w-24" />
          </div>
          <div className="space-y-1 text-right">
            <Skeleton className="h-3 w-16 ml-auto" />
            <Skeleton className="h-3 w-12 ml-auto" />
          </div>
        </div>
      ))}
    </div>
  );
}

// Re-export types for backward compatibility
export type { WorkflowLogSummary as WorkflowLog };
