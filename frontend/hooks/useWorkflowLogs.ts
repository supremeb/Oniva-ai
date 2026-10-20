/**
 * TanStack Query hooks for workflow logs.
 *
 * Provides:
 * - useWorkflowLogs: Paginated log list with filtering
 * - useWorkflowLogDetail: Single log detail with execution trace
 * - useLogStream: SSE connection for real-time updates
 * - useMonitoringMetrics: Enterprise monitoring dashboard data
 */

import { useQuery, useQueryClient, useInfiniteQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { isActive, normalizeStatus, type RunStatus } from '@/lib/run/status';
import {
  fetchWorkflowLogs,
  fetchWorkflowLogDetail,
  fetchWaitingNodes,
  fetchMonitoringMetrics,
  fetchTopWorkflows,
  fetchRecentFailures,
  fetchMonitoringTimeSeries,
  getLogStreamUrl,
  LogFilterParams,
  WorkflowLogsResponse,
  WorkflowLogSummary,
  TimeRange,
} from '@/lib/api/workflow-logs';

// ============================================
// Polling cadence
// ============================================

/** Poll interval while a run is moving (running, resuming, queued). */
export const POLL_ACTIVE_MS = 3_000;
/** Poll interval while every unfinished run is parked (an approval, a sleep): nothing changes until it wakes. */
export const POLL_PARKED_MS = 20_000;

/** How often to refresh while the given run statuses are on screen; `false` when all are finished. */
export function pollIntervalFor(statuses: readonly RunStatus[]): number | false {
  const active = statuses.filter(isActive);
  if (active.length === 0) return false;
  return active.every((s) => s === 'waiting') ? POLL_PARKED_MS : POLL_ACTIVE_MS;
}

// ============================================
// Log List Hooks
// ============================================

/**
 * Hook for fetching paginated workflow logs with filtering.
 *
 * Features:
 * - 30s stale time to reduce unnecessary refetches
 * - Auto-refetch every 3s while a run is moving (20s when all are waiting)
 * - Prefetches next page using cursor
 */
export function useWorkflowLogs(
  workflowId: string | null,
  params: Omit<LogFilterParams, 'cursor'> = {}
) {
  const queryClient = useQueryClient();

  // Skip fetching for local workflows (they don't have backend logs)
  const isValidWorkflowId = !!workflowId && !workflowId.startsWith('local-');

  const query = useQuery({
    queryKey: ['workflow-logs', workflowId, params],
    queryFn: () => fetchWorkflowLogs(workflowId!, params),
    enabled: isValidWorkflowId,
    staleTime: 30_000, // 30 seconds
    gcTime: 5 * 60 * 1000, // 5 minutes (formerly cacheTime)
    refetchInterval: (query) => {
      // 3s while any run is moving, 20s when every unfinished run is waiting, off when all are finished
      return pollIntervalFor((query.state.data?.logs ?? []).map((log) => normalizeStatus(log.status, 'workflow_log')));
    },
  });

  // Prefetch next page using cursor
  useEffect(() => {
    if (query.data?.has_more && query.data?.next_cursor && workflowId) {
      queryClient.prefetchQuery({
        queryKey: ['workflow-logs', workflowId, { ...params, cursor: query.data.next_cursor }],
        queryFn: () => fetchWorkflowLogs(workflowId, { ...params, cursor: query.data.next_cursor! }),
        staleTime: 30_000,
      });
    }
  }, [query.data?.has_more, query.data?.next_cursor, workflowId, params, queryClient]);

  return query;
}

/**
 * Hook for infinite scrolling through workflow logs.
 *
 * Uses cursor-based pagination for O(1) performance.
 */
export function useWorkflowLogsInfinite(
  workflowId: string | null,
  params: Omit<LogFilterParams, 'cursor'> = {}
) {
  // Skip fetching for local workflows (they don't have backend logs)
  const isValidWorkflowId = !!workflowId && !workflowId.startsWith('local-');

  return useInfiniteQuery({
    queryKey: ['workflow-logs-infinite', workflowId, params],
    queryFn: ({ pageParam }) =>
      fetchWorkflowLogs(workflowId!, { ...params, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: isValidWorkflowId,
    staleTime: 30_000,
    gcTime: 5 * 60 * 1000,
  });
}

// ============================================
// Log Detail Hook
// ============================================

/**
 * Hook for fetching detailed execution trace for a specific run.
 */
export function useWorkflowLogDetail(
  workflowId: string | null,
  runId: string | null
) {
  // Skip fetching for local workflows (they don't have backend logs)
  const isValidWorkflowId = !!workflowId && !workflowId.startsWith('local-');

  return useQuery({
    queryKey: ['workflow-log-detail', workflowId, runId],
    queryFn: () => fetchWorkflowLogDetail(workflowId!, runId!),
    enabled: isValidWorkflowId && !!runId,
    staleTime: 30_000,
    gcTime: 5 * 60 * 1000,
    // An unfinished run keeps refreshing (its trace grows and its status settles); a finished one stops.
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status ? pollIntervalFor([normalizeStatus(status, 'workflow_log')]) : false;
    },
  });
}

/**
 * Nodes of a run parked on a human decision (plan 003 U21). Polls while enabled; an error (403 for someone who
 * may not see the run, 404) simply yields no waiting nodes.
 */
export function useWaitingNodes(executionId: string | null, enabled: boolean, pollMs: number = POLL_ACTIVE_MS) {
  return useQuery({
    queryKey: ['workflow-waiting-nodes', executionId],
    queryFn: () => fetchWaitingNodes(executionId!),
    enabled: enabled && !!executionId,
    staleTime: 0,
    retry: false,
    refetchInterval: pollMs,
  });
}

// ============================================
// SSE Stream Hook
// ============================================

/**
 * Hook for real-time log updates via SSE.
 *
 * Features:
 * - Automatic reconnection with exponential backoff
 * - Heartbeat handling
 * - Optimistic cache updates via setQueryData
 */
export function useLogStream(workflowId: string | null) {
  const queryClient = useQueryClient();
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectAttempts = useRef(0);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  const connect = useCallback(() => {
    // Skip SSE connection for local workflows (they don't have backend logs)
    if (!workflowId || workflowId.startsWith('local-')) return;

    // Clear any pending reconnect
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }

    const url = getLogStreamUrl(workflowId);
    const eventSource = new EventSource(url);

    eventSource.onopen = () => {
      setIsConnected(true);
      reconnectAttempts.current = 0; // Reset on successful connection
    };

    eventSource.onmessage = (event) => {
      try {
        const logUpdate = JSON.parse(event.data) as WorkflowLogSummary;

        // Update the logs list cache using setQueryData
        queryClient.setQueryData(
          ['workflow-logs', workflowId],
          (old: WorkflowLogsResponse | undefined) => {
            if (!old) return old;

            const existingIndex = old.logs.findIndex((l) => l.id === logUpdate.id);

            if (existingIndex >= 0) {
              // Update existing log immutably
              const newLogs = [...old.logs];
              newLogs[existingIndex] = logUpdate;
              return { ...old, logs: newLogs };
            } else {
              // Add new log at the top
              return {
                ...old,
                logs: [logUpdate, ...old.logs.slice(0, old.limit - 1)],
              };
            }
          }
        );
      } catch {
        // Ignore parse errors for non-JSON messages
      }
    };

    // Handle heartbeat events (keep-alive)
    eventSource.addEventListener('heartbeat', () => {
      // Heartbeat received - connection is healthy
    });

    // Handle timeout events
    eventSource.addEventListener('timeout', () => {
      eventSource.close();
      setIsConnected(false);
    });

    // Handle errors with exponential backoff
    eventSource.onerror = () => {
      setIsConnected(false);
      eventSource.close();

      // Exponential backoff: 1s, 2s, 4s, 8s, max 30s
      const backoffMs = Math.min(1000 * Math.pow(2, reconnectAttempts.current), 30_000);
      reconnectAttempts.current++;

      console.warn(`SSE disconnected, reconnecting in ${backoffMs}ms (attempt ${reconnectAttempts.current})`);

      reconnectTimeoutRef.current = setTimeout(() => {
        connect();
      }, backoffMs);
    };

    eventSourceRef.current = eventSource;
  }, [workflowId, queryClient]);

  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    setIsConnected(false);
  }, []);

  useEffect(() => {
    connect();
    return disconnect;
  }, [connect, disconnect]);

  return { isConnected, reconnect: connect, disconnect };
}

// ============================================
// Monitoring Hooks
// ============================================

/**
 * Hook for fetching monitoring metrics.
 */
export function useMonitoringMetrics(
  timeRange: TimeRange = '24h',
  workflowId?: string
) {
  return useQuery({
    queryKey: ['monitoring-metrics', timeRange, workflowId],
    queryFn: () => fetchMonitoringMetrics(timeRange, workflowId),
    staleTime: 30_000, // 30 seconds - metrics can be slightly stale
    gcTime: 5 * 60 * 1000,
    refetchInterval: 30_000, // Auto-refresh every 30s
  });
}

/**
 * Hook for fetching top workflows by execution count.
 */
export function useTopWorkflows(
  timeRange: TimeRange = '24h',
  limit: number = 10
) {
  return useQuery({
    queryKey: ['top-workflows', timeRange, limit],
    queryFn: () => fetchTopWorkflows(timeRange, limit),
    staleTime: 30_000,
    gcTime: 5 * 60 * 1000,
    refetchInterval: 30_000,
  });
}

/**
 * Hook for fetching recent failures.
 */
export function useRecentFailures(limit: number = 10) {
  return useQuery({
    queryKey: ['recent-failures', limit],
    queryFn: () => fetchRecentFailures(limit),
    staleTime: 30_000,
    gcTime: 5 * 60 * 1000,
    refetchInterval: 30_000,
  });
}

/**
 * Hook for fetching monitoring time-series data (sparklines).
 */
export function useMonitoringTimeSeries(
  workflowId: string | null,
  timeRange: TimeRange = '24h'
) {
  const isValid = !!workflowId && !workflowId.startsWith('local-');
  return useQuery({
    queryKey: ['monitoring-time-series', workflowId, timeRange],
    queryFn: () => fetchMonitoringTimeSeries(workflowId!, timeRange),
    enabled: isValid,
    staleTime: 30_000,
    gcTime: 5 * 60 * 1000,
    refetchInterval: 30_000,
  });
}
