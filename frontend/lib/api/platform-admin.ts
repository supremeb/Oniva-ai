/**
 * Platform Admin API client for tenant feature management.
 *
 * These endpoints require is_platform_operator=true.
 */
import { apiFetch } from './index';

export interface TenantSummary {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
  feature_count: number;
}

export interface AdminFeatureMap {
  features: Record<string, boolean>;
  overrides: Record<string, boolean>;
  metadata: Record<string, {
    group: string;
    description: string;
    operator_only: boolean;
  }>;
}

export async function listTenants(): Promise<TenantSummary[]> {
  return apiFetch<TenantSummary[]>('/api/v1/admin/tenants');
}

export async function getTenantFeatures(tenantId: string): Promise<AdminFeatureMap> {
  return apiFetch<AdminFeatureMap>(`/api/v1/admin/tenants/${tenantId}/features`);
}

export async function toggleTenantFeature(
  tenantId: string,
  flagName: string,
  enabled: boolean
): Promise<AdminFeatureMap> {
  return apiFetch<AdminFeatureMap>(
    `/api/v1/admin/tenants/${tenantId}/features/${flagName}`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    }
  );
}

export async function getFeatureMetadata(): Promise<Record<string, {
  group: string;
  description: string;
  operator_only: boolean;
}>> {
  return apiFetch<Record<string, { group: string; description: string; operator_only: boolean }>>(
    '/api/v1/admin/feature-metadata'
  );
}
