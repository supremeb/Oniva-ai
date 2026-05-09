import { create } from 'zustand';
import type {
  BackgroundJob,
  ExecutionTraceStep,
  JobStatus,
  JobProgressEvent,
  JobCompletedEvent,
} from '@/lib/types/chat-panels';

interface BackgroundJobState {
  // Active jobs being tracked
  activeJobs: Record<string, BackgroundJob>; // keyed by job ID

  // Jobs by context for quick lookup
  jobsByContext: Record<string, string[]>; // context key -> job IDs

  // Event sources for SSE connections
  eventSources: Record<string, EventSource | null>;

  // UI state
  expandedJobId: string | null;
  showCompletedJobs: boolean;
}

interface BackgroundJobActions {
  // Job management
  addJob: (job: BackgroundJob) => void;
  updateJob: (jobId: string, updates: Partial<BackgroundJob>) => void;
  updateJobProgress: (jobId: string, progress: number, step?: ExecutionTraceStep) => void;
  completeJob: (jobId: string, result?: Record<string, unknown>, error?: string) => void;
  removeJob: (jobId: string) => void;

  // Context management
  getJobsForContext: (contextKey: string) => BackgroundJob[];
  getActiveJobForContext: (contextKey: string) => BackgroundJob | undefined;

  // SSE streaming
  startJobStream: (jobId: string, onProgress?: (event: JobProgressEvent) => void, onComplete?: (event: JobCompletedEvent) => void) => void;
  stopJobStream: (jobId: string) => void;
  stopAllStreams: () => void;

  // UI actions
  setExpandedJob: (jobId: string | null) => void;
  toggleShowCompleted: () => void;

  // Helpers
  getContextKeyForJob: (job: BackgroundJob) => string;
  reset: () => void;
}

type BackgroundJobStore = BackgroundJobState & BackgroundJobActions;

const initialState: BackgroundJobState = {
  activeJobs: {},
  jobsByContext: {},
  eventSources: {},
  expandedJobId: null,
  showCompletedJobs: false,
};

export const useBackgroundJobStore = create<BackgroundJobStore>((set, get) => ({
  ...initialState,

  // Job management
  addJob: (job) =>
    set((state) => {
      const contextKey = get().getContextKeyForJob(job);
      const existingJobIds = state.jobsByContext[contextKey] || [];

      return {
        activeJobs: { ...state.activeJobs, [job.id]: job },
        jobsByContext: {
          ...state.jobsByContext,
          [contextKey]: [...existingJobIds, job.id],
        },
      };
    }),

  updateJob: (jobId, updates) =>
    set((state) => {
      const job = state.activeJobs[jobId];
      if (!job) return state;

      return {
        activeJobs: {
          ...state.activeJobs,
          [jobId]: { ...job, ...updates },
        },
      };
    }),

  updateJobProgress: (jobId, progress, step) =>
    set((state) => {
      const job = state.activeJobs[jobId];
      if (!job) return state;

      const updatedJob = {
        ...job,
        progress,
        status: 'running' as JobStatus,
      };

      if (step) {
        // Update or add the step in execution trace
        const existingIndex = job.execution_trace.findIndex(
          (s) => s.step_id === step.step_id
        );
        if (existingIndex >= 0) {
          updatedJob.execution_trace = [
            ...job.execution_trace.slice(0, existingIndex),
            step,
            ...job.execution_trace.slice(existingIndex + 1),
          ];
        } else {
          updatedJob.execution_trace = [...job.execution_trace, step];
        }
      }

      return {
        activeJobs: { ...state.activeJobs, [jobId]: updatedJob },
      };
    }),

  completeJob: (jobId, result, error) =>
    set((state) => {
      const job = state.activeJobs[jobId];
      if (!job) return state;

      const status: JobStatus = error ? 'failed' : 'completed';

      return {
        activeJobs: {
          ...state.activeJobs,
          [jobId]: {
            ...job,
            status,
            progress: status === 'completed' ? 100 : job.progress,
            result: result || job.result,
            error: error || job.error,
            completed_at: new Date().toISOString(),
          },
        },
      };
    }),

  removeJob: (jobId) =>
    set((state) => {
      const job = state.activeJobs[jobId];
      if (!job) return state;

      const contextKey = get().getContextKeyForJob(job);
      const { [jobId]: _, ...restJobs } = state.activeJobs;
      const contextJobIds = (state.jobsByContext[contextKey] || []).filter(
        (id) => id !== jobId
      );

      // Stop any active stream
      const eventSource = state.eventSources[jobId];
      if (eventSource) {
        eventSource.close();
      }
      const { [jobId]: __, ...restEventSources } = state.eventSources;

      return {
        activeJobs: restJobs,
        jobsByContext: {
          ...state.jobsByContext,
          [contextKey]: contextJobIds,
        },
        eventSources: restEventSources,
        expandedJobId: state.expandedJobId === jobId ? null : state.expandedJobId,
      };
    }),

  // Context management
  getJobsForContext: (contextKey) => {
    const state = get();
    const jobIds = state.jobsByContext[contextKey] || [];
    return jobIds
      .map((id) => state.activeJobs[id])
      .filter((job): job is BackgroundJob => job !== undefined);
  },

  getActiveJobForContext: (contextKey) => {
    const jobs = get().getJobsForContext(contextKey);
    return jobs.find((job) => job.status === 'running' || job.status === 'pending');
  },

  // SSE streaming
  startJobStream: (jobId, onProgress, onComplete) => {
    const state = get();

    // Don't create duplicate streams
    if (state.eventSources[jobId]) {
      return;
    }

    const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
    const eventSource = new EventSource(`${apiUrl}/api/v1/background-jobs/${jobId}/stream`, {
      withCredentials: true,
    });

    eventSource.addEventListener('progress', (event) => {
      try {
        const data = JSON.parse(event.data) as JobProgressEvent;
        get().updateJobProgress(data.job_id, data.progress, data.current_step);
        onProgress?.(data);
      } catch (e) {
        console.error('Failed to parse progress event:', e);
      }
    });

    eventSource.addEventListener('step', (event) => {
      try {
        const step = JSON.parse(event.data) as ExecutionTraceStep;
        const job = get().activeJobs[jobId];
        if (job) {
          get().updateJob(jobId, {
            execution_trace: [...job.execution_trace, step],
          });
        }
      } catch (e) {
        console.error('Failed to parse step event:', e);
      }
    });

    eventSource.addEventListener('completed', (event) => {
      try {
        const data = JSON.parse(event.data) as JobCompletedEvent;
        get().completeJob(data.job_id, data.result);
        onComplete?.(data);
        get().stopJobStream(jobId);
      } catch (e) {
        console.error('Failed to parse completed event:', e);
      }
    });

    eventSource.addEventListener('failed', (event) => {
      try {
        const data = JSON.parse(event.data) as JobCompletedEvent;
        get().completeJob(data.job_id, undefined, data.error);
        onComplete?.(data);
        get().stopJobStream(jobId);
      } catch (e) {
        console.error('Failed to parse failed event:', e);
      }
    });

    eventSource.addEventListener('cancelled', (event) => {
      try {
        const data = JSON.parse(event.data) as JobCompletedEvent;
        get().updateJob(data.job_id, {
          status: 'cancelled',
          error: data.error,
          completed_at: data.completed_at,
        });
        onComplete?.(data);
        get().stopJobStream(jobId);
      } catch (e) {
        console.error('Failed to parse cancelled event:', e);
      }
    });

    let retryCount = 0;
    const MAX_RETRIES = 5;

    eventSource.onerror = () => {
      retryCount++;
      console.warn(`SSE connection error for job ${jobId} (attempt ${retryCount}/${MAX_RETRIES})`);

      if (retryCount >= MAX_RETRIES) {
        console.error(`SSE permanently failed for job ${jobId} after ${MAX_RETRIES} retries`);
        get().stopJobStream(jobId);
        // Mark job with connection error so UI can show banner
        get().updateJob(jobId, {
          error: 'Lost connection to server. Refresh to check job status.',
        });
        return;
      }

      // EventSource auto-reconnects; reset counter on next successful message
    };

    // Reset retry counter on any successful message
    const resetRetries = () => { retryCount = 0; };
    eventSource.addEventListener('progress', resetRetries);
    eventSource.addEventListener('step', resetRetries);

    set((state) => ({
      eventSources: { ...state.eventSources, [jobId]: eventSource },
    }));
  },

  stopJobStream: (jobId) =>
    set((state) => {
      const eventSource = state.eventSources[jobId];
      if (eventSource) {
        eventSource.close();
      }
      const { [jobId]: _, ...rest } = state.eventSources;
      return { eventSources: rest };
    }),

  stopAllStreams: () => {
    const state = get();
    Object.values(state.eventSources).forEach((es) => es?.close());
    set({ eventSources: {} });
  },

  // UI actions
  setExpandedJob: (jobId) => set({ expandedJobId: jobId }),

  toggleShowCompleted: () =>
    set((state) => ({ showCompletedJobs: !state.showCompletedJobs })),

  // Helpers
  getContextKeyForJob: (job) => {
    if (job.job_type === 'deep_work' && job.session_id) return `deep_work:${job.session_id}`;
    if (job.session_id) return `research:${job.session_id}`;
    if (job.workflow_id) return `workflow:${job.workflow_id}`;
    if (job.agent_id) return `agent:${job.agent_id}`;
    return 'unknown';
  },

  reset: () => {
    get().stopAllStreams();
    set(initialState);
  },
}));

// Selectors
export const selectJob = (jobId: string) => (state: BackgroundJobStore) =>
  state.activeJobs[jobId];

// Memoized selector to prevent infinite re-renders
// The key issue is that Object.values().filter() creates a new array every time
// We need to return the same array reference when contents haven't changed
let cachedActiveJobs: BackgroundJob[] = [];
let cachedActiveJobsKey = '';

export const selectActiveJobs = (state: BackgroundJobStore): BackgroundJob[] => {
  // Create a stable key from active job IDs and statuses
  const activeJobEntries = Object.entries(state.activeJobs)
    .filter(([_, job]) => job.status === 'running' || job.status === 'pending')
    .sort(([a], [b]) => a.localeCompare(b));

  const key = activeJobEntries.map(([id, job]) => `${id}:${job.status}:${job.progress}`).join('|');

  // Return cached result if nothing changed
  if (key === cachedActiveJobsKey) {
    return cachedActiveJobs;
  }

  // Update cache
  cachedActiveJobsKey = key;
  cachedActiveJobs = activeJobEntries.map(([_, job]) => job);
  return cachedActiveJobs;
};

export const selectJobsForWorkflow = (workflowId: string) => (state: BackgroundJobStore) =>
  state.getJobsForContext(`workflow:${workflowId}`);

export const selectJobsForAgent = (agentId: string) => (state: BackgroundJobStore) =>
  state.getJobsForContext(`agent:${agentId}`);

export const selectJobsForResearch = (sessionId: string) => (state: BackgroundJobStore) =>
  state.getJobsForContext(`research:${sessionId}`);

export const selectHasActiveJob = (contextKey: string) => (state: BackgroundJobStore) =>
  state.getActiveJobForContext(contextKey) !== undefined;
