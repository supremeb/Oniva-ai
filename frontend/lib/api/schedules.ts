/**
 * API functions for Workflow Scheduling
 *
 * Manages cron-based, interval-based, and one-time scheduled workflow executions.
 */
import { apiFetch } from './index';

// ============================================================================
// Types
// ============================================================================

export interface ScheduleResponse {
  id: string;
  workflow_id: string;
  name: string;
  description: string | null;
  schedule_type: 'cron' | 'interval' | 'one_time';
  cron_expression: string | null;
  interval_seconds: number | null;
  scheduled_time: string | null;
  timezone: string;
  status: 'active' | 'paused' | 'disabled' | 'completed' | 'error';
  is_enabled: boolean;
  input_variables: Record<string, unknown>;
  max_executions: number | null;
  execution_count: number;
  max_concurrent: number;
  timeout_seconds: number;
  retry_on_failure: boolean;
  max_retries: number;
  retry_delay_seconds: number;
  last_run_at: string | null;
  last_run_status: string | null;
  last_error: string | null;
  next_run_at: string | null;
  human_readable_schedule: string;
  created_at: string;
  updated_at: string;
}

export interface ScheduleCreate {
  workflow_id: string;
  name: string;
  description?: string;
  schedule_type: 'cron' | 'interval' | 'one_time';
  cron_expression?: string;
  interval_seconds?: number;
  scheduled_time?: string;
  timezone?: string;
  input_variables?: Record<string, unknown>;
  max_executions?: number;
  max_concurrent?: number;
  timeout_seconds?: number;
  retry_on_failure?: boolean;
  max_retries?: number;
  retry_delay_seconds?: number;
}

export interface ScheduleUpdate {
  name?: string;
  description?: string;
  schedule_type?: 'cron' | 'interval' | 'one_time';
  cron_expression?: string;
  interval_seconds?: number;
  scheduled_time?: string;
  timezone?: string;
  input_variables?: Record<string, unknown>;
  max_executions?: number;
  max_concurrent?: number;
  timeout_seconds?: number;
  retry_on_failure?: boolean;
  max_retries?: number;
  retry_delay_seconds?: number;
}

export interface PaginatedScheduleResponse {
  items: ScheduleResponse[];
  total: number;
  skip: number;
  limit: number;
  has_more: boolean;
}

export interface ScheduleExecutionResponse {
  id: string;
  schedule_id: string;
  workflow_execution_id: string | null;
  scheduled_for: string;
  started_at: string | null;
  completed_at: string | null;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  result_summary: Record<string, unknown>;
  error: string | null;
  retry_count: number;
  created_at: string;
}

export interface PaginatedExecutionResponse {
  items: ScheduleExecutionResponse[];
  total: number;
  skip: number;
  limit: number;
  has_more: boolean;
}

export interface TriggerResponse {
  message: string;
  execution_id: string;
  scheduled_for: string;
}

// ============================================================================
// API Functions
// ============================================================================

export async function listSchedules(filters?: {
  workflow_id?: string;
  status?: string;
  skip?: number;
  limit?: number;
}): Promise<PaginatedScheduleResponse> {
  const params = new URLSearchParams();
  if (filters?.workflow_id) params.append('workflow_id', filters.workflow_id);
  if (filters?.status) params.append('status', filters.status);
  if (filters?.skip != null) params.append('skip', String(filters.skip));
  if (filters?.limit != null) params.append('limit', String(filters.limit));
  const query = params.toString();
  return apiFetch<PaginatedScheduleResponse>(`/api/v1/schedules${query ? `?${query}` : ''}`);
}

export async function getSchedule(id: string): Promise<ScheduleResponse> {
  return apiFetch<ScheduleResponse>(`/api/v1/schedules/${id}`);
}

export async function createSchedule(data: ScheduleCreate): Promise<ScheduleResponse> {
  return apiFetch<ScheduleResponse>('/api/v1/schedules', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateSchedule(id: string, data: ScheduleUpdate): Promise<ScheduleResponse> {
  return apiFetch<ScheduleResponse>(`/api/v1/schedules/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function deleteSchedule(id: string): Promise<{ id: string; message: string }> {
  return apiFetch(`/api/v1/schedules/${id}`, { method: 'DELETE' });
}

export async function pauseSchedule(id: string): Promise<ScheduleResponse> {
  return apiFetch<ScheduleResponse>(`/api/v1/schedules/${id}/pause`, { method: 'POST' });
}

export async function resumeSchedule(id: string): Promise<ScheduleResponse> {
  return apiFetch<ScheduleResponse>(`/api/v1/schedules/${id}/resume`, { method: 'POST' });
}

export async function triggerSchedule(id: string): Promise<TriggerResponse> {
  return apiFetch<TriggerResponse>(`/api/v1/schedules/${id}/trigger`, { method: 'POST' });
}

export async function listScheduleExecutions(
  scheduleId: string,
  filters?: { status?: string; skip?: number; limit?: number }
): Promise<PaginatedExecutionResponse> {
  const params = new URLSearchParams();
  if (filters?.status) params.append('status', filters.status);
  if (filters?.skip != null) params.append('skip', String(filters.skip));
  if (filters?.limit != null) params.append('limit', String(filters.limit));
  const query = params.toString();
  return apiFetch<PaginatedExecutionResponse>(
    `/api/v1/schedules/${scheduleId}/executions${query ? `?${query}` : ''}`
  );
}
