import { apiFetch } from './index';
import { APIKey, CreateAPIKeyRequest, Connector, CreateConnectorRequest } from '@/types/api';

/**
 * API Key Management
 */

interface APIKeyListResponse {
  api_keys: APIKey[];
  total: number;
}

export async function getAPIKeys(): Promise<APIKey[]> {
  const response = await apiFetch<APIKeyListResponse>('/api/v1/settings/api-keys');
  // Handle both wrapped response {api_keys: [...]} and direct array [...]
  if (Array.isArray(response)) {
    return response;
  }
  return response?.api_keys ?? [];
}

export async function getAPIKey(id: string): Promise<APIKey> {
  return apiFetch<APIKey>(`/api/v1/settings/api-keys/${id}`);
}

export async function createAPIKey(data: CreateAPIKeyRequest): Promise<APIKey> {
  return apiFetch<APIKey>('/api/v1/settings/api-keys', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateAPIKey(
  id: string,
  data: Partial<CreateAPIKeyRequest>
): Promise<APIKey> {
  return apiFetch<APIKey>(`/api/v1/settings/api-keys/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteAPIKey(id: string): Promise<void> {
  return apiFetch<void>(`/api/v1/settings/api-keys/${id}`, {
    method: 'DELETE',
  });
}

/**
 * Connector Management
 */

interface ConnectorListResponse {
  connectors: Connector[];
  total: number;
}

export async function getConnectors(): Promise<Connector[]> {
  const response = await apiFetch<ConnectorListResponse>('/api/v1/settings/connectors');
  // Handle both wrapped response {connectors: [...]} and direct array [...]
  if (Array.isArray(response)) {
    return response;
  }
  return response?.connectors ?? [];
}

export async function getConnector(id: string): Promise<Connector> {
  return apiFetch<Connector>(`/api/v1/settings/connectors/${id}`);
}

export async function createConnector(data: CreateConnectorRequest): Promise<Connector> {
  return apiFetch<Connector>('/api/v1/settings/connectors', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateConnector(
  id: string,
  data: Partial<CreateConnectorRequest>
): Promise<Connector> {
  return apiFetch<Connector>(`/api/v1/settings/connectors/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteConnector(id: string): Promise<void> {
  return apiFetch<void>(`/api/v1/settings/connectors/${id}`, {
    method: 'DELETE',
  });
}

export async function testConnector(id: string): Promise<{ success: boolean; message: string }> {
  return apiFetch<{ success: boolean; message: string }>(
    `/api/v1/settings/connectors/${id}/test`,
    {
      method: 'POST',
    }
  );
}
