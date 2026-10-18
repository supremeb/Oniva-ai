/**
 * useFleetRuns — TanStack Query wrapper for fleet run listing.
 *
 * Query key: ['fleet-runs', params] — prefix match allows
 * invalidateQueries({ queryKey: ['fleet-runs'] }) to refetch all variants.
 */

import { useQuery } from '@tanstack/react-query';
import { APIError } from '@/lib/api';
import { getFleetRun, getFleetRuns } from '@/lib/api/fleet';
import type { FleetRunItem, FleetRunsResponse } from '@/lib/models';

export interface UseFleetRunsParams {
  status?: string;
  agent_id?: string;
  page?: number;
  page_size?: number;
}

export function useFleetRuns(params?: UseFleetRunsParams, options?: { enabled?: boolean }) {
  return useQuery<FleetRunsResponse>({
    queryKey: ['fleet-runs', params],
    queryFn: () => getFleetRuns(params),
    enabled: options?.enabled ?? true,
  });
}

/** Find one run by id; `null` means it is gone or someone else's (404). */
export async function findFleetRun(runId: string): Promise<FleetRunItem | null> {
  try {
    return await getFleetRun(runId);
  } catch (e) {
    if (e instanceof APIError && e.status === 404) return null;
    throw e;
  }
}

/**
 * One run, live: the row (conversation id included) stays fresh while the job is pending or has no conversation yet,
 * so a run picked before it started switches to the engine watch once the backend assigns its conversation.
 */
export function useFleetRun(runId: string | null) {
  return useQuery<FleetRunItem | null>({
    queryKey: ['fleet-run', runId],
    queryFn: () => findFleetRun(runId!),
    enabled: !!runId,
    retry: false,
    refetchInterval: (q) => {
      const row = q.state.data;
      return row && !row.conversation_id && (row.status === 'pending' || row.status === 'running') && row.engine !== false ? 3_000 : false;
    },
  });
}
