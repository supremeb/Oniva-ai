"use client";

import type { QueryClient } from '@tanstack/react-query';

/**
 * Query invalidation utilities for TanStack Query
 *
 * Use these when users report stale data issues
 */

/**
 * Force refetch all queries
 * Use when user reports stale data across the app
 */
export function invalidateAllQueries(queryClient: QueryClient): void {
  queryClient.invalidateQueries();
  console.log('[QueryCache] Invalidated all queries');
}

/**
 * Clear the entire query cache
 * Nuclear option - use when invalidation doesn't fix the issue
 */
export function clearQueryCache(queryClient: QueryClient): void {
  queryClient.clear();
  console.log('[QueryCache] Cleared entire cache');
}

/**
 * Invalidate queries by key prefix
 * More targeted than invalidating everything
 *
 * @example
 * invalidateQueriesByPrefix(queryClient, 'workflows');
 * invalidateQueriesByPrefix(queryClient, 'agents');
 */
export function invalidateQueriesByPrefix(
  queryClient: QueryClient,
  prefix: string
): void {
  queryClient.invalidateQueries({
    predicate: (query) => {
      const key = query.queryKey;
      if (Array.isArray(key) && key.length > 0 && typeof key[0] === 'string') {
        return key[0].startsWith(prefix);
      }
      return false;
    },
  });
  console.log(`[QueryCache] Invalidated queries with prefix "${prefix}"`);
}

/**
 * Invalidate specific query keys
 *
 * @example
 * invalidateQueryKeys(queryClient, ['workflows', 'agents', 'connectors']);
 */
export function invalidateQueryKeys(
  queryClient: QueryClient,
  keys: string[]
): void {
  keys.forEach(key => {
    queryClient.invalidateQueries({ queryKey: [key] });
  });
  console.log(`[QueryCache] Invalidated queries: ${keys.join(', ')}`);
}

/**
 * Get cache statistics for debugging
 */
export function getQueryCacheStats(queryClient: QueryClient): {
  totalQueries: number;
  activeQueries: number;
  staleQueries: number;
  queryKeys: string[];
} {
  const cache = queryClient.getQueryCache();
  const queries = cache.getAll();

  const activeQueries = queries.filter(q => q.state.status === 'pending').length;
  const staleQueries = queries.filter(q => q.isStale()).length;

  return {
    totalQueries: queries.length,
    activeQueries,
    staleQueries,
    queryKeys: queries.map(q => JSON.stringify(q.queryKey)),
  };
}

/**
 * Log query cache stats to console
 */
export function logQueryCacheStats(queryClient: QueryClient): void {
  const stats = getQueryCacheStats(queryClient);
  console.group('[QueryCache Stats]');
  console.log('Total queries:', stats.totalQueries);
  console.log('Active queries:', stats.activeQueries);
  console.log('Stale queries:', stats.staleQueries);
  console.log('Query keys:', stats.queryKeys);
  console.groupEnd();
}

/**
 * Refetch all stale queries
 * Less aggressive than invalidateAll - only refetches what's already stale
 */
export function refetchStaleQueries(queryClient: QueryClient): void {
  queryClient.refetchQueries({ stale: true });
  console.log('[QueryCache] Refetching stale queries');
}

/**
 * Common query key prefixes used in the app
 */
export const QUERY_KEY_PREFIXES = {
  WORKFLOWS: 'workflows',
  AGENTS: 'agents',
  CONNECTORS: 'connectors',
  API_KEYS: 'api-keys',
  SKILLS: 'skills',
  WORKFLOW_LOGS: 'workflow-logs',
} as const;

export type QueryKeyPrefix = typeof QUERY_KEY_PREFIXES[keyof typeof QUERY_KEY_PREFIXES];
