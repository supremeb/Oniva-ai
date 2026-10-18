/**
 * Workflow Logs API client functions.
 *
 * Provides typed API calls for:
 * - Fetching paginated workflow execution logs
 * - Getting detailed execution traces
 * - Monitoring metrics endpoints
 */

import { apiFetch } from './index';

// ============================================
// Types
// ============================================

export type LogStatus = 'pending' | 'queued' | 'running' | 'waiting' | 'resuming' | 'completed' | 'failed' | 'cancelled' | 'skipped' | 'interrupted';
export type LogTrigger = 'manual' | 'api' | 'scheduled';
export type TimeRange = '1h' | '24h' | '7d' | '30d';

export interface ExecutionTraceStep {
  step_id: string;
  node_id: string;
  node_type: string;
  node_name: string | null;
  status: 'pending' | 'running' | 'waiting' | 'completed' | 'failed' | 'cancelled' | 'skipped' | 'interrupted';
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  error: string | null;
  token_usage: { prompt: number; completion: number; total: number } | null;
  reasoning: string | null;
}

export interface WorkflowLogSummary {
  id: string;
  workflow_id: string;
  workflow_version_id: string | null;
  version_number: number | null;
  status: LogStatus;
  trigger: LogTrigger;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  total_tokens: number;
  nodes_executed: number;
  error: string | null;
  progress: number;
  /** The engine conversation running this execution; null/absent for legacy runs. */
  engine_conversation_id?: string | null;
}

export interface WorkflowLogDetail extends WorkflowLogSummary {
  input_data: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  execution_trace: ExecutionTraceStep[];
}

export interface LogFilterParams {
  status?: 'running' | 'success' | 'failed';
  trigger?: LogTrigger;
  time_range?: TimeRange;
  search?: string;
  version?: number;
  cursor?: string;
  limit?: number;
}

export interface WorkflowLogsResponse {
  logs: WorkflowLogSummary[];
  next_cursor: string | null;
  limit: number;
  has_more: boolean;
}

// Monitoring types
export interface MonitoringMetrics {
  time_range: string;
  total_executions: number;
  successful: number;
  failed: number;
  running: number;
  success_rate: number;
  avg_duration_ms: number | null;
}

export interface TopWorkflow {
  workflow_id: string;
  workflow_name: string | null;
  execution_count: number;
  failure_count: number;
  success_rate: number;
}

export interface TopWorkflowsResponse {
  workflows: TopWorkflow[];
}

export interface RecentFailure {
  id: string;
  workflow_id: string;
  workflow_name: string | null;
  error: string;
  failed_at: string;
  trigger: string;
}

export interface RecentFailuresResponse {
  failures: RecentFailure[];
}

// ============================================
// API Functions
// ============================================

/**
 * Fetch paginated workflow execution logs with filtering.
 */
export async function fetchWorkflowLogs(
  workflowId: string,
  params: LogFilterParams = {}
): Promise<WorkflowLogsResponse> {
  const searchParams = new URLSearchParams();

  if (params.status) searchParams.set('status', params.status);
  if (params.trigger) searchParams.set('trigger', params.trigger);
  if (params.time_range) searchParams.set('time_range', params.time_range);
  if (params.search) searchParams.set('search', params.search);
  if (params.version) searchParams.set('version', params.version.toString());
  if (params.cursor) searchParams.set('cursor', params.cursor);
  if (params.limit) searchParams.set('limit', params.limit.toString());

  const queryString = searchParams.toString();
  const url = `/api/v1/workflows/${workflowId}/logs${queryString ? `?${queryString}` : ''}`;

  return apiFetch<WorkflowLogsResponse>(url);
}

/**
 * Fetch the latest completed execution for a workflow.
 * Used to restore node statuses and results on page refresh.
 * Returns null if no completed execution exists.
 */
export async function fetchLatestExecution(
  workflowId: string
): Promise<WorkflowLogDetail | null> {
  try {
    return await apiFetch<WorkflowLogDetail>(
      `/api/v1/workflows/${workflowId}/latest-execution`
    );
  } catch {
    // 404 = no completed execution yet
    return null;
  }
}

/**
 * Fetch detailed execution trace for a specific workflow run.
 */
export async function fetchWorkflowLogDetail(
  workflowId: string,
  runId: string
): Promise<WorkflowLogDetail> {
  return apiFetch<WorkflowLogDetail>(`/api/v1/workflows/${workflowId}/logs/${runId}`);
}

/** A node of a run parked on a human decision (`GET /workflows/executions/{id}/waiting-nodes`, plan 003 U21). */
export interface WaitingNode {
  nodeId: string | null;
  kind: string;
  title: string;
  status: string;
  /** ISO wake time (the SLA), or null when open-ended. */
  wakeAt: string | null;
  approvalId: string;
  conversationId: string | null;
  taskId: string | null;
}

export async function fetchWaitingNodes(executionId: string): Promise<WaitingNode[]> {
  const res = await apiFetch<{ executionId: string; nodes: WaitingNode[] }>(
    `/api/v1/workflows/executions/${executionId}/waiting-nodes`
  );
  return res.nodes ?? [];
}

/**
 * Fetch monitoring metrics for the tenant.
 */
export async function fetchMonitoringMetrics(
  timeRange: TimeRange = '24h',
  workflowId?: string
): Promise<MonitoringMetrics> {
  const params = new URLSearchParams({ time_range: timeRange });
  if (workflowId) params.set('workflow_id', workflowId);

  return apiFetch<MonitoringMetrics>(`/api/v1/workflows/monitoring/metrics?${params}`);
}

/**
 * Fetch top workflows by execution count.
 */
export async function fetchTopWorkflows(
  timeRange: TimeRange = '24h',
  limit: number = 10
): Promise<TopWorkflowsResponse> {
  const params = new URLSearchParams({
    time_range: timeRange,
    limit: limit.toString(),
  });

  return apiFetch<TopWorkflowsResponse>(`/api/v1/workflows/monitoring/top-workflows?${params}`);
}

/**
 * Fetch recent workflow failures.
 */
export async function fetchRecentFailures(
  limit: number = 10
): Promise<RecentFailuresResponse> {
  return apiFetch<RecentFailuresResponse>(
    `/api/v1/workflows/monitoring/recent-failures?limit=${limit}`
  );
}

// Time series types
export interface TimeSeriesPoint {
  period: string;
  count: number;
  successful: number;
  failed: number;
}

export interface MonitoringTimeSeries {
  points: TimeSeriesPoint[];
  granularity: string;
}

/**
 * Fetch monitoring time-series data for sparklines.
 */
export async function fetchMonitoringTimeSeries(
  workflowId: string,
  timeRange: TimeRange = '24h'
): Promise<MonitoringTimeSeries> {
  return apiFetch<MonitoringTimeSeries>(
    `/api/v1/workflows/${workflowId}/monitoring/time-series?time_range=${timeRange}`
  );
}

/**
 * Get SSE stream URL for real-time log updates.
 * For same-origin deployments, cookies are sent automatically by EventSource.
 * The token query param is kept as an optional fallback for cross-origin dev mode.
 */
export function getLogStreamUrl(workflowId: string): string {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
  const url = `${baseUrl}/api/v1/workflows/${workflowId}/logs/stream`;
  // Token query param is optional - cookies are the primary auth mechanism.
  // Keep as fallback for cross-origin dev mode where cookies may not be sent.
  return token ? `${url}?token=${encodeURIComponent(token)}` : url;
}
