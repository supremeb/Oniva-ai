/**
 * Connector Marketplace API Client
 *
 * Functions for browsing, enabling, and managing OpenAPI connectors.
 */

import { apiFetch, APIError } from './index';

// ========== Types ==========

export interface ConnectorCategory {
  id: string;
  name: string;
  icon: string;
  description: string;
  connector_count: number;
}

export interface MarketplaceConnector {
  id: string;
  name: string;
  description: string;
  category: string;
  auth_type: string;
  icon: string;
  docs_url?: string;
  operation_count?: number;
  is_enabled: boolean;
}

export interface MarketplaceConnectorDetail extends MarketplaceConnector {
  spec_url?: string;
  sample_tools: Array<{
    name: string;
    description: string;
    input_schema: Record<string, unknown>;
  }>;
  required_credentials: string[];
}

export interface EnableConnectorRequest {
  credentials: Record<string, string>;
  selected_operations?: string[];
  name?: string;
}

export interface EnableConnectorResponse {
  id: string;
  connector_id: string;
  name: string;
  status: string;
  operation_count: number;
  message: string;
}

export interface ImportOpenAPIRequest {
  name: string;
  spec_url?: string;
  spec_content?: Record<string, unknown>;
  auth_type?: string;
  auth_location?: string;
  auth_header_name?: string;
  category?: string;
}

export interface ImportOpenAPIResponse {
  id: string;
  name: string;
  description?: string;
  operation_count: number;
  status: string;
  message: string;
}

export interface ConnectorOperation {
  name: string;
  description: string;
  method: string;
  path: string;
  parameters: string[];
  tags: string[];
}

export interface ConnectorOperationsResponse {
  connector_id: string;
  connector_name: string;
  operations: ConnectorOperation[];
  total_count: number;
}

export interface TestConnectionResponse {
  status: 'healthy' | 'unhealthy' | 'unauthenticated' | 'error';
  message: string;
  latency_ms?: number;
  tested_at: string;
}

export interface EnabledConnector {
  id: string;
  connector_id: string;
  name: string;
  description?: string;
  category: string;
  icon: string;
  auth_type: string;
  source_type: 'native' | 'curated' | 'custom';
  operation_count: number;
  is_active: boolean;
  is_verified: boolean;
  last_verified_at?: string;
  created_at: string;
  updated_at: string;
}

// ========== API Functions ==========

/**
 * List all connector categories with counts.
 */
export async function listCategories(): Promise<ConnectorCategory[]> {
  return apiFetch<ConnectorCategory[]>('/api/v1/marketplace/categories');
}

/**
 * List connectors from the curated registry.
 */
export async function listConnectors(params?: {
  category?: string;
  search?: string;
}): Promise<MarketplaceConnector[]> {
  const queryParams = new URLSearchParams();
  if (params?.category) queryParams.set('category', params.category);
  if (params?.search) queryParams.set('search', params.search);

  const query = queryParams.toString();
  const endpoint = query
    ? `/api/v1/marketplace/connectors?${query}`
    : '/api/v1/marketplace/connectors';

  return apiFetch<MarketplaceConnector[]>(endpoint);
}

/**
 * Get detailed info about a connector including sample tools.
 */
export async function getConnectorDetail(
  connectorId: string
): Promise<MarketplaceConnectorDetail> {
  return apiFetch<MarketplaceConnectorDetail>(
    `/api/v1/marketplace/connectors/${connectorId}`
  );
}

/**
 * Enable a connector from the marketplace.
 */
export async function enableConnector(
  connectorId: string,
  request: EnableConnectorRequest
): Promise<EnableConnectorResponse> {
  return apiFetch<EnableConnectorResponse>(
    `/api/v1/marketplace/connectors/${connectorId}/enable`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    }
  );
}

/**
 * Import a custom OpenAPI spec.
 */
export async function importCustomSpec(
  request: ImportOpenAPIRequest
): Promise<ImportOpenAPIResponse> {
  return apiFetch<ImportOpenAPIResponse>('/api/v1/marketplace/import', {
    method: 'POST',
    body: JSON.stringify(request),
  });
}

/**
 * List all operations available for a connector.
 */
export async function getConnectorOperations(
  connectorId: string
): Promise<ConnectorOperationsResponse> {
  return apiFetch<ConnectorOperationsResponse>(
    `/api/v1/marketplace/connectors/${connectorId}/operations`
  );
}

/**
 * Test connectivity for an enabled connector.
 */
export async function testConnection(
  connectorUuid: string,
  credentials?: Record<string, string>
): Promise<TestConnectionResponse> {
  return apiFetch<TestConnectionResponse>(
    `/api/v1/marketplace/${connectorUuid}/test`,
    {
      method: 'POST',
      body: JSON.stringify({ credentials }),
    }
  );
}

/**
 * List all connectors enabled for the current tenant.
 */
export async function listEnabledConnectors(): Promise<{
  connectors: EnabledConnector[];
  total_count: number;
}> {
  return apiFetch<{ connectors: EnabledConnector[]; total_count: number }>(
    '/api/v1/marketplace/enabled'
  );
}

/**
 * Disable (soft-delete) a connector.
 */
export async function disableConnector(
  connectorUuid: string
): Promise<{ status: string; message: string }> {
  return apiFetch<{ status: string; message: string }>(
    `/api/v1/marketplace/${connectorUuid}`,
    { method: 'DELETE' }
  );
}
