/**
 * LLM Provider API client
 *
 * Phase 2: LLM Reliability - Provider Settings
 *
 * CRUD operations for tenant-specific LLM provider configuration.
 */

import { apiFetch } from './index';

// ============================================================================
// Types
// ============================================================================

export interface LLMProvider {
  id: string;
  name: string;
  display_name: string;
  provider_type: string;
  api_base: string | null;
  has_api_key: boolean;
  models: string[];
  default_model: string | null;
  embedding_model: string | null;
  workflow_builder_model: string | null;
  priority: number;
  is_enabled: boolean;
  is_default: boolean;
}

export interface CreateLLMProviderRequest {
  name: string;
  display_name: string;
  provider_type: string;
  api_base?: string | null;
  api_key?: string | null;
  models?: string[];
  default_model?: string | null;
  embedding_model?: string | null;
  workflow_builder_model?: string | null;
  priority?: number;
  is_enabled?: boolean;
}

export interface UpdateLLMProviderRequest {
  display_name?: string;
  api_base?: string | null;
  api_key?: string | null;
  models?: string[];
  default_model?: string | null;
  embedding_model?: string | null;
  workflow_builder_model?: string | null;
  priority?: number;
  is_enabled?: boolean;
}

export interface TestProviderRequest {
  provider_type: string;
  api_base?: string | null;
  api_key: string;
  model?: string;
}

export interface TestProviderResponse {
  success: boolean;
  model?: string | null;
  message?: string | null;
  error?: string | null;
}

interface ProviderListResponse {
  providers: LLMProvider[];
  total: number;
}

// ============================================================================
// API Functions
// ============================================================================

/**
 * List all LLM providers for the current tenant.
 */
export async function getLLMProviders(): Promise<LLMProvider[]> {
  const response = await apiFetch<ProviderListResponse>('/api/v1/settings/llm-providers');
  return response.providers ?? [];
}

/**
 * Get a specific LLM provider by ID.
 */
export async function getLLMProvider(id: string): Promise<LLMProvider> {
  return apiFetch<LLMProvider>(`/api/v1/settings/llm-providers/${id}`);
}

/**
 * Create a new LLM provider.
 */
export async function createLLMProvider(data: CreateLLMProviderRequest): Promise<LLMProvider> {
  return apiFetch<LLMProvider>('/api/v1/settings/llm-providers', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Update an existing LLM provider.
 */
export async function updateLLMProvider(
  id: string,
  data: UpdateLLMProviderRequest
): Promise<LLMProvider> {
  return apiFetch<LLMProvider>(`/api/v1/settings/llm-providers/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

/**
 * Delete an LLM provider.
 */
export async function deleteLLMProvider(id: string): Promise<void> {
  return apiFetch<void>(`/api/v1/settings/llm-providers/${id}`, {
    method: 'DELETE',
  });
}

/**
 * Test connection to an LLM provider.
 */
export async function testLLMProvider(data: TestProviderRequest): Promise<TestProviderResponse> {
  return apiFetch<TestProviderResponse>('/api/v1/settings/llm-providers/test', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Reorder providers by setting priority based on list order.
 */
export async function reorderLLMProviders(providerIds: string[]): Promise<{ status: string; count: number }> {
  return apiFetch<{ status: string; count: number }>('/api/v1/settings/llm-providers/reorder', {
    method: 'POST',
    body: JSON.stringify(providerIds),
  });
}

// ============================================================================
// Browse Models (fetch from provider catalog)
// ============================================================================

export interface BrowseModelInfo {
  id: string;
  name: string;
  context_length?: number;
  pricing_prompt?: string;
  pricing_completion?: string;
}

export async function browseAvailableModels(providerType: string = 'openrouter'): Promise<BrowseModelInfo[]> {
  const response = await apiFetch<{ models: BrowseModelInfo[]; total: number }>(
    `/api/v1/settings/llm-providers/browse-models?provider_type=${providerType}`,
    { timeout: 20000 },
  );
  return response.models ?? [];
}

// ============================================================================
// LLM Usage Types
// ============================================================================

export interface UsageSummary {
  total_requests: number;
  total_tokens: number;
  total_cost_usd: number;
  avg_latency_ms: number;
}

export interface DailyUsage {
  date: string;
  requests: number;
  tokens: number;
  cost_usd: number;
}

export interface ProviderUsage {
  provider_name: string;
  requests: number;
  tokens: number;
  cost_usd: number;
}

export interface ModelUsage {
  model: string;
  provider_name: string;
  requests: number;
  tokens: number;
  cost_usd: number;
}

export interface UsageResponse {
  period_start: string;
  period_end: string;
  summary: UsageSummary;
  by_date: DailyUsage[];
  by_provider: ProviderUsage[];
  by_model: ModelUsage[];
}

// ============================================================================
// LLM Usage API Functions
// ============================================================================

/**
 * Get LLM usage summary with breakdowns.
 */
export async function getLLMUsage(days: number = 30): Promise<UsageResponse> {
  return apiFetch<UsageResponse>(`/api/v1/llm-usage/summary?days=${days}`);
}

/**
 * Get LLM usage for a specific workflow.
 */
export async function getWorkflowLLMUsage(workflowId: string): Promise<UsageSummary> {
  return apiFetch<UsageSummary>(`/api/v1/llm-usage/workflow/${workflowId}`);
}
