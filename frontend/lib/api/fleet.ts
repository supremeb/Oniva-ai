/**
 * API functions for Fleet (background agent monitoring)
 */
import { apiFetch } from './index';
import type { FleetRunsResponse, InterventionResponse } from '@/lib/models';

/**
 * Get paginated fleet runs with optional filters
 */
export async function getFleetRuns(params?: {
  status?: string;
  agent_id?: string;
  page?: number;
  page_size?: number;
}): Promise<FleetRunsResponse> {
  const searchParams = new URLSearchParams();
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined) {
        searchParams.append(key, String(value));
      }
    });
  }
  const query = searchParams.toString();
  return apiFetch<FleetRunsResponse>(`/api/v1/fleet/runs${query ? `?${query}` : ''}`);
}

/**
 * Send an intervention message to a running background agent
 */
export async function interveneInRun(
  jobId: string,
  message: string
): Promise<InterventionResponse> {
  return apiFetch<InterventionResponse>(`/api/v1/fleet/runs/${jobId}/intervene`, {
    method: 'POST',
    body: JSON.stringify({ message }),
  });
}
