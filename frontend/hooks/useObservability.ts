/**
 * TanStack Query hooks for the Observability Dashboard.
 *
 * Provides:
 * - useDashboardMetrics: LLM latency, error rate, token usage
 * - useTraceSearch: Cross-workflow trace search with pagination
 * - useAllAgentsHealth: Bulk agent health with circuit breaker + accuracy
 * - useAlertHistory: Recent alert deliveries for bell badge
 */

import { useQuery } from '@tanstack/react-query';
import {
  fetchDashboardMetrics,
  searchTraces,
  fetchAllAgentsHealth,
  type TraceSearchResponse,
} from '@/lib/api/observability';
import { fetchAlertHistory, type AlertHistoryItem } from '@/lib/api/alerts';

// ============================================
// Dashboard Metrics Hook
// ============================================

export function useDashboardMetrics(windowMinutes: number = 60) {
  return useQuery({
    queryKey: ['observability-dashboard-metrics', windowMinutes],
    queryFn: () => fetchDashboardMetrics(windowMinutes),
    staleTime: 30_000,
    gcTime: 5 * 60 * 1000,
    refetchInterval: 30_000,
    retry: 1,
  });
}

// ============================================
// Trace Search Hook
// ============================================

export function useTraceSearch(params: {
  status?: string;
  workflow_id?: string;
  date_from?: string;
  date_to?: string;
  offset?: number;
  limit?: number;
}) {
  return useQuery({
    queryKey: ['observability-traces', params],
    queryFn: () => searchTraces(params),
    staleTime: 30_000,
    gcTime: 5 * 60 * 1000,
    retry: 1,
  });
}

// ============================================
// Agent Health Hook
// ============================================

export function useAllAgentsHealth() {
  return useQuery({
    queryKey: ['observability-agent-health'],
    queryFn: () => fetchAllAgentsHealth(),
    staleTime: 30_000,
    gcTime: 5 * 60 * 1000,
    refetchInterval: 30_000,
    retry: 1,
  });
}

// ============================================
// Alert History Hook
// ============================================

export function useAlertHistory(limit: number = 50) {
  return useQuery({
    queryKey: ['alert-history', limit],
    queryFn: () => fetchAlertHistory(limit),
    staleTime: 30_000,
    gcTime: 5 * 60 * 1000,
    refetchInterval: 60_000, // Poll every 60s for new alerts
    retry: 1,
  });
}

/**
 * Hook to get unread alert count for the bell badge.
 */
export function useUnreadAlertCount() {
  const { data } = useAlertHistory(10);
  // Count alerts from the last 24 hours as "unread"
  const cutoff = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
  const count = data?.filter((a) => a.created_at > cutoff).length ?? 0;
  return count;
}
