/**
 * API functions for background jobs
 */
import { apiFetch, API_BASE_URL } from './index';
import type {
  BackgroundJob,
  BackgroundJobCreate,
  BackgroundJobListResponse,
  JobStatus,
  JobType,
  JobProgressEvent,
  JobCompletedEvent,
} from '@/lib/types/chat-panels';

/**
 * Create a new background job
 */
export async function createBackgroundJob(data: BackgroundJobCreate): Promise<BackgroundJob> {
  return apiFetch<BackgroundJob>('/api/v1/background-jobs/', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * Get jobs with optional filters
 */
export async function getBackgroundJobs(params?: {
  status?: JobStatus;
  job_type?: JobType;
  workflow_id?: string;
  agent_id?: string;
  page?: number;
  page_size?: number;
}): Promise<BackgroundJobListResponse> {
  const searchParams = new URLSearchParams();
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined) {
        searchParams.append(key, String(value));
      }
    });
  }
  const query = searchParams.toString();
  return apiFetch<BackgroundJobListResponse>(`/api/v1/background-jobs/${query ? `?${query}` : ''}`);
}

/**
 * Get active (running/pending) jobs
 */
export async function getActiveJobs(): Promise<BackgroundJobListResponse> {
  return apiFetch<BackgroundJobListResponse>('/api/v1/background-jobs/active');
}

/**
 * Get a specific job
 */
export async function getBackgroundJob(jobId: string): Promise<BackgroundJob> {
  return apiFetch<BackgroundJob>(`/api/v1/background-jobs/${jobId}`);
}

/**
 * Cancel a job
 */
export async function cancelJob(
  jobId: string,
  reason?: string
): Promise<{
  success: boolean;
  job_id: string;
  status: string;
  message: string;
}> {
  return apiFetch(`/api/v1/background-jobs/${jobId}/cancel`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
}

/**
 * Delete a completed job
 */
export async function deleteJob(jobId: string): Promise<void> {
  return apiFetch<void>(`/api/v1/background-jobs/${jobId}`, {
    method: 'DELETE',
  });
}

/**
 * Get job history for a workflow
 */
export async function getWorkflowJobHistory(
  workflowId: string,
  limit?: number
): Promise<BackgroundJobListResponse> {
  const query = limit ? `?limit=${limit}` : '';
  return apiFetch<BackgroundJobListResponse>(
    `/api/v1/background-jobs/workflow/${workflowId}/history${query}`
  );
}

/**
 * Get job history for an agent
 */
export async function getAgentJobHistory(
  agentId: string,
  limit?: number
): Promise<BackgroundJobListResponse> {
  const query = limit ? `?limit=${limit}` : '';
  return apiFetch<BackgroundJobListResponse>(
    `/api/v1/background-jobs/agent/${agentId}/history${query}`
  );
}

/**
 * Create an SSE connection to stream job progress with automatic reconnect.
 *
 * Reconnect strategy: exponential backoff (1s, 2s, 4s, 8s, 16s) capped at 30s,
 * max 5 retries. Resets retry counter on first successful message.
 */
export function createJobStream(
  jobId: string,
  callbacks: {
    onProgress?: (event: JobProgressEvent) => void;
    onStep?: (step: Record<string, unknown>) => void;
    onCompleted?: (event: JobCompletedEvent) => void;
    onFailed?: (event: JobCompletedEvent) => void;
    onCancelled?: (event: JobCompletedEvent) => void;
    onError?: (error: Error) => void;
  }
): () => void {
  const MAX_RETRIES = 5;
  const MAX_DELAY_MS = 30_000;

  let source: EventSource | null = null;
  let retries = 0;
  let stopped = false;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;

  function connect() {
    if (stopped) return;

    source = new EventSource(`${API_BASE_URL}/api/v1/background-jobs/${jobId}/stream`, {
      withCredentials: true,
    });

    source.addEventListener('progress', (event) => {
      try {
        retries = 0; // healthy connection confirmed
        const data = JSON.parse(event.data) as JobProgressEvent;
        callbacks.onProgress?.(data);
      } catch (e) {
        console.error('Failed to parse progress event:', e);
      }
    });

    source.addEventListener('step', (event) => {
      try {
        retries = 0;
        const step = JSON.parse(event.data);
        callbacks.onStep?.(step);
      } catch (e) {
        console.error('Failed to parse step event:', e);
      }
    });

    source.addEventListener('completed', (event) => {
      try {
        const data = JSON.parse(event.data) as JobCompletedEvent;
        callbacks.onCompleted?.(data);
      } catch (e) {
        console.error('Failed to parse completed event:', e);
      } finally {
        source?.close();
        source = null;
      }
    });

    source.addEventListener('failed', (event) => {
      try {
        const data = JSON.parse(event.data) as JobCompletedEvent;
        callbacks.onFailed?.(data);
      } catch (e) {
        console.error('Failed to parse failed event:', e);
      } finally {
        source?.close();
        source = null;
      }
    });

    source.addEventListener('cancelled', (event) => {
      try {
        const data = JSON.parse(event.data) as JobCompletedEvent;
        callbacks.onCancelled?.(data);
      } catch (e) {
        console.error('Failed to parse cancelled event:', e);
      } finally {
        source?.close();
        source = null;
      }
    });

    source.onerror = () => {
      source?.close();
      source = null;

      if (!stopped && retries < MAX_RETRIES) {
        const delay = Math.min(1000 * Math.pow(2, retries), MAX_DELAY_MS);
        retries++;
        retryTimer = setTimeout(connect, delay);
      } else if (!stopped) {
        callbacks.onError?.(new Error('SSE connection error after max retries'));
      }
    };
  }

  connect();

  // Return cleanup function
  return () => {
    stopped = true;
    if (retryTimer) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
    source?.close();
    source = null;
  };
}
