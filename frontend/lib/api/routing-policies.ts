/**
 * Routing Policies API client — admin-configured task type → model mapping.
 */
import { apiFetch } from './index';

export interface RoutingPolicy {
  id: string;
  tenant_id: string;
  name: string;
  task_type: string;
  output_type: string | null;
  model_id: string;
  provider: string;
  priority: number;
  max_tokens: number | null;
  temperature: number | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface RoutingPolicyCreate {
  name: string;
  task_type: string;
  output_type?: string | null;
  model_id: string;
  provider: string;
  priority?: number;
  max_tokens?: number | null;
  temperature?: number | null;
  is_active?: boolean;
}

export interface RoutingPolicyUpdate {
  name?: string;
  task_type?: string;
  output_type?: string | null;
  model_id?: string;
  provider?: string;
  priority?: number;
  max_tokens?: number | null;
  temperature?: number | null;
  is_active?: boolean;
}

export async function listRoutingPolicies(): Promise<RoutingPolicy[]> {
  return apiFetch<RoutingPolicy[]>('/api/v1/routing-policies/');
}

export async function createRoutingPolicy(body: RoutingPolicyCreate): Promise<RoutingPolicy> {
  return apiFetch<RoutingPolicy>('/api/v1/routing-policies/', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function updateRoutingPolicy(id: string, body: RoutingPolicyUpdate): Promise<RoutingPolicy> {
  return apiFetch<RoutingPolicy>(`/api/v1/routing-policies/${id}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

export async function deleteRoutingPolicy(id: string): Promise<void> {
  await apiFetch(`/api/v1/routing-policies/${id}`, { method: 'DELETE' });
}

export async function resetRoutingDefaults(): Promise<RoutingPolicy[]> {
  return apiFetch<RoutingPolicy[]>('/api/v1/routing-policies/reset-defaults', {
    method: 'POST',
  });
}
