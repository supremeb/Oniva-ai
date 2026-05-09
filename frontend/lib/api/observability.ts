/**
 * Observability API client functions.
 */
import { apiFetch } from './index';

// ============================================
// Types
// ============================================

export interface LatencyMetrics {
  p50: number;
  p95: number;
  p99: number;
  count: number;
  mean: number;
}

export interface ErrorRateMetrics {
  total_calls: number;
  error_count: number;
  error_rate: number;
  success_rate: number;
}

export interface TokenUsageByGroup {
  group: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  calls: number;
  cost_usd: number;
  avg_latency_ms: number;
}

export interface ObservabilityDashboardMetrics {
  latency: LatencyMetrics;
  error_rate: ErrorRateMetrics;
  token_usage_by_model: TokenUsageByGroup[];
  window_minutes: number;
  generated_at: string;
}

export interface ExecutionTraceStep {
  // Workflow orchestrator fields
  step_id?: string | null;
  node_id?: string | null;
  node_type?: string | null;
  node_name?: string | null;
  status?: string | null;
  input?: Record<string, unknown> | null;
  output?: Record<string, unknown> | null;
  error?: string | null;
  reasoning?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
  token_usage?: { input?: number; output?: number; total?: number } | null;
  // Agent executor fields
  step?: number | null;
  type?: string | null; // "tool_call" | "text_response"
  tool_name?: string | null;
  tool_args?: Record<string, unknown> | null;
  tool_result_preview?: string | null;
  content_preview?: string | null;
  timestamp?: string | null;
}

export interface TraceSearchResult {
  run_id: string;
  workflow_id: string | null;
  workflow_name: string | null;
  agent_name: string | null;
  source: string;
  status: string;
  created_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  total_steps: number;
  execution_trace: ExecutionTraceStep[];
  error: string | null;
  input_data: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
}

export interface LLMCallTrace {
  id: string;
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_ms: number;
  status: string;
  agent_id: string | null;
  workflow_id: string | null;
  created_at: string;
}

export interface TraceSearchResponse {
  traces: TraceSearchResult[];
  llm_calls: LLMCallTrace[];
  total: number;
}

export interface AgentHealthSummary {
  agent_id: string;
  agent_name: string;
  agent_type: string;
  circuit_breaker_status: 'healthy' | 'degraded' | 'unknown';
  accuracy: number | null;
  has_drift: boolean | null;
  last_evaluation_at: string | null;
}

export interface AllAgentsHealthResponse {
  agents: AgentHealthSummary[];
  total: number;
}

// ============================================
// API Functions
// ============================================

export async function fetchDashboardMetrics(
  windowMinutes: number = 60
): Promise<ObservabilityDashboardMetrics> {
  return apiFetch<ObservabilityDashboardMetrics>(
    `/api/v1/observability/dashboard-metrics?window_minutes=${windowMinutes}`
  );
}

export async function searchTraces(params: {
  status?: string;
  workflow_id?: string;
  date_from?: string;
  date_to?: string;
  offset?: number;
  limit?: number;
}): Promise<TraceSearchResponse> {
  const searchParams = new URLSearchParams();
  if (params.status) searchParams.set('status', params.status);
  if (params.workflow_id) searchParams.set('workflow_id', params.workflow_id);
  if (params.date_from) searchParams.set('date_from', params.date_from);
  if (params.date_to) searchParams.set('date_to', params.date_to);
  if (params.offset !== undefined) searchParams.set('offset', String(params.offset));
  if (params.limit !== undefined) searchParams.set('limit', String(params.limit));
  return apiFetch<TraceSearchResponse>(
    `/api/v1/observability/traces?${searchParams.toString()}`
  );
}

export async function fetchAllAgentsHealth(): Promise<AllAgentsHealthResponse> {
  return apiFetch<AllAgentsHealthResponse>('/api/v1/observability/agent-health');
}
