/**
 * useFleetRuns — TanStack Query wrapper for fleet run listing.
 *
 * Query key: ['fleet-runs', params] — prefix match allows
 * invalidateQueries({ queryKey: ['fleet-runs'] }) to refetch all variants.
 */

import { useQuery } from '@tanstack/react-query';
import { getFleetRuns } from '@/lib/api/fleet';
import type { FleetRunsResponse } from '@/lib/models';

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
