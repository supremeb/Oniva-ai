/**
 * Analytics API functions for cost attribution dashboard.
 *
 * Enterprise feature: Cost visibility and attribution for billing,
 * optimization, and chargeback.
 */
import { apiFetch } from './index';

// --- Types ---

export interface CostDataPoint {
  period: string;
  total_cost_usd: number;
  total_tokens: number;
  api_calls: number;
  avg_latency_ms: number | null;
}

export interface WorkflowCostBreakdown {
  workflow_id: string | null;
  workflow_name: string | null;
  total_cost_usd: number;
  total_tokens: number;
  execution_count: number;
  avg_cost_per_execution: number;
}

export interface UserCostBreakdown {
  user_id: string;
  user_name: string | null;
  user_email: string | null;
  total_cost_usd: number;
  total_tokens: number;
  workflow_count: number;
}

export interface ModelCostBreakdown {
  provider_name: string;
  model: string;
  total_cost_usd: number;
  total_tokens: number;
  api_calls: number;
  avg_cost_per_call: number;
}

export interface CostSummaryResponse {
  start_date: string;
  end_date: string;
  total_cost_usd: number;
  total_tokens: number;
  total_api_calls: number;
  avg_cost_per_day: number;
  trends: CostDataPoint[];
}

// --- API Functions ---

function formatDate(date: Date): string {
  return date.toISOString();
}

/**
 * Get cost summary with time series trends.
 */
export async function getCostSummary(
  startDate: Date,
  endDate: Date,
  groupBy: 'hour' | 'day' | 'week' | 'month' = 'day'
): Promise<CostSummaryResponse> {
  const params = new URLSearchParams({
    start_date: formatDate(startDate),
    end_date: formatDate(endDate),
    group_by: groupBy,
  });

  return apiFetch<CostSummaryResponse>(`/api/v1/analytics/cost-summary?${params}`);
}

/**
 * Get top workflows by cost.
 */
export async function getCostByWorkflow(
  startDate: Date,
  endDate: Date,
  limit: number = 20
): Promise<WorkflowCostBreakdown[]> {
  const params = new URLSearchParams({
    start_date: formatDate(startDate),
    end_date: formatDate(endDate),
    limit: String(limit),
  });

  return apiFetch<WorkflowCostBreakdown[]>(`/api/v1/analytics/cost-by-workflow?${params}`);
}

/**
 * Get cost breakdown by user.
 */
export async function getCostByUser(
  startDate: Date,
  endDate: Date,
  limit: number = 20
): Promise<UserCostBreakdown[]> {
  const params = new URLSearchParams({
    start_date: formatDate(startDate),
    end_date: formatDate(endDate),
    limit: String(limit),
  });

  return apiFetch<UserCostBreakdown[]>(`/api/v1/analytics/cost-by-user?${params}`);
}

/**
 * Get cost breakdown by provider and model.
 */
export async function getCostByModel(
  startDate: Date,
  endDate: Date,
  limit: number = 20
): Promise<ModelCostBreakdown[]> {
  const params = new URLSearchParams({
    start_date: formatDate(startDate),
    end_date: formatDate(endDate),
    limit: String(limit),
  });

  return apiFetch<ModelCostBreakdown[]>(`/api/v1/analytics/cost-by-model?${params}`);
}

/**
 * Get URL for cost data CSV export.
 */
export function getCostExportUrl(startDate: Date, endDate: Date): string {
  const params = new URLSearchParams({
    start_date: formatDate(startDate),
    end_date: formatDate(endDate),
  });

  return `/api/v1/analytics/cost-export?${params}`;
}
