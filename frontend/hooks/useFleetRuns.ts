/**
 * useFleetRuns — TanStack Query wrapper for fleet run listing.
 *
 * Query key: ['fleet-runs', params] — prefix match allows
 * invalidateQueries({ queryKey: ['fleet-runs'] }) to refetch all variants.
 */

import { useQuery } from '@tanstack/react-query';
import { getFleetRuns } from '@/lib/api/fleet';
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

const FIND_PAGE_SIZE = 100;
const FIND_MAX_PAGES = 10;

/**
 * Find one run by id. The fleet API has no single-run route, so scan the newest-first list page by page (bounded);
 * `null` means the run is not in the tenant's newest runs (gone, or someone else's).
 */
export async function findFleetRun(runId: string): Promise<FleetRunItem | null> {
  for (let page = 1; page <= FIND_MAX_PAGES; page++) {
    const res = await getFleetRuns({ page, page_size: FIND_PAGE_SIZE });
    const hit = res.items.find((r) => r.id === runId);
    if (hit) return hit;
    if (page * FIND_PAGE_SIZE >= res.total || res.items.length === 0) break;
  }
  return null;
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
