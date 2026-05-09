/**
 * Deep Work Zustand store — manages autonomous execution state.
 *
 * Pre-execution state (clarifying, planning, awaiting_approval) is persisted
 * to localStorage via Zustand persist middleware so it survives page refresh.
 * Execution state is durable on the backend — on chat open, the frontend
 * fetches full state from GET /deep-work/by-conversation/{id}/full-state
 * and hydrates via hydrateFromBackend(). SSE provides live updates during
 * execution.
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type {
  DeepWorkStatus,
  DeepWorkTodo,
  DeepWorkPlan,
  DeepWorkStep,
  DeepWorkArtifact,
  DeepWorkSource,
  DeepWorkConnector,
  WorkspaceFile,
  EngineWorkerState,
  EngineReflection,
  EnginePhase,
  IntakeResult,
} from '@/lib/types/deep-work';
import type { StructuredQuestion } from '@/lib/models';
import {
  executeDeepWork,
  sendHumanInput,
  pauseSession,
  resumeSession,
  stopSession,
  buildEventsUrl,
  getSessionStatus,
  listWorkspaceFiles,
  startEngine,
  cancelEngine,
  buildEngineEventsUrl,
  generateEnginePlan,
  clarifyQuery,
} from '@/lib/api/deep-work';

// ── SSE reconnect token refresh tracking ──
// Proactive token refresh is now handled globally by useTokenRefresh hook.
// This counter is only for SSE reconnect-on-401 error recovery.
let _tokenRefreshReconnects = 0;
const MAX_TOKEN_REFRESH_RECONNECTS = 3;

/** Shape returned by GET /deep-work/by-conversation/{id}/full-state */
export interface DeepWorkFullState {
  session_id: string;
  conversation_id: string | null;
  status: string;
  plan: { title: string; steps: string[] } | null;
  todos: DeepWorkTodo[];
  steps: Record<string, unknown>[];
  workspace_files: Array<{ name: string; path: string; size: number; type: string; modified_at: number }>;
  summary: string | null;
  key_findings: string[];
  sources: DeepWorkSource[];
  connectors: DeepWorkConnector[];
  artifacts?: Array<{ artifact_uri?: string; download_url?: string; filename?: string; size?: number }>;
  is_engine: boolean;
  engine_report: string | null;
  engine_phase: string | null;
  error: string | null;
  workers: Array<{
    worker_id: string;
    sub_query: string;
    status?: string;
    learning_count?: number;
    source_count?: number;
    duration_seconds?: number;
  }> | null;
}

interface DeepWorkState {
  // Session
  sessionId: string | null;
  jobId: string | null;
  conversationId: string | null;
  status: DeepWorkStatus;

  // Plan
  plan: DeepWorkPlan | null;
  todos: DeepWorkTodo[];

  // Execution
  steps: DeepWorkStep[];
  currentStep: number;
  totalSteps: number;
  iterationCount: number;
  elapsedSeconds: number;

  // Human input
  pendingQuestion: string | StructuredQuestion | null;

  // Workspace files
  workspaceFiles: WorkspaceFile[];

  // Activity
  currentActivity: string | null;

  // Sources & connectors
  sources: DeepWorkSource[];
  connectors: DeepWorkConnector[];

  // Results
  summary: string | null;
  keyFindings: string[];
  artifacts: DeepWorkArtifact[];

  // Error
  error: string | null;

  // SSE (non-serializable)
  eventSource: EventSource | null;

  // ── Intake clarification ──────────────────────────────────
  intakeResult: IntakeResult | null;

  // Persistence timestamp (for stale cleanup)
  persistedAt: number | null;

  // ── Engine (orchestrator-worker) state — additive ──────────
  engineMode: boolean;
  enginePlan: { sub_queries: string[] } | null;
  engineRawPlan: Record<string, unknown> | null;
  enginePendingQuery: string | null;
  engineWorkers: Record<string, EngineWorkerState>;
  engineReport: string;
  engineReflection: EngineReflection | null;
  engineStartedAt: number | null;
  engineElapsedSeconds: number;
  enginePhase: EnginePhase | null;
}

interface DeepWorkActions {
  startSession: (agentId: string, plan: DeepWorkPlan, message: string, conversationId?: string) => Promise<void>;
  respondToQuestion: (input: string) => Promise<void>;
  approvePlan: () => void;
  rejectPlan: () => void;
  pause: () => Promise<void>;
  resume: () => Promise<void>;
  stop: () => Promise<void>;
  connectEvents: (sessionId: string) => void;
  disconnectEvents: () => void;
  /** Hydrate store from backend full-state response */
  hydrateFromBackend: (fullState: DeepWorkFullState) => void;
  reset: () => void;
  // ── Intake actions ─────────────────────────────────────────
  clarify: (query: string, conversationId?: string, availableKbs?: string[], availableConnectors?: string[]) => Promise<void>;
  skipClarification: (enrichedQuery?: string) => void;
  // ── Engine actions ──────────────────────────────────────────
  setDeliverable: (type: string | null, description?: string) => void;
  generatePlan: (query: string, conversationId?: string, enrichedQuery?: string) => Promise<void>;
  approveEnginePlan: () => Promise<void>;
  rejectEnginePlan: () => void;
  startEngineSession: (query: string, conversationId?: string) => Promise<void>;
  cancelEngineSession: () => Promise<void>;
  connectEngineEvents: (sessionId: string) => void;
}

type DeepWorkStore = DeepWorkState & DeepWorkActions;

const initialState: DeepWorkState = {
  sessionId: null,
  jobId: null,
  conversationId: null,
  status: 'idle',
  plan: null,
  todos: [],
  steps: [],
  currentStep: 0,
  totalSteps: 0,
  iterationCount: 0,
  elapsedSeconds: 0,
  pendingQuestion: null,
  workspaceFiles: [],
  currentActivity: null,
  sources: [],
  connectors: [],
  summary: null,
  keyFindings: [],
  artifacts: [],
  error: null,
  eventSource: null,
  // Intake
  intakeResult: null,
  // Persistence
  persistedAt: null,
  // Engine
  engineMode: false,
  enginePlan: null,
  engineRawPlan: null,
  enginePendingQuery: null,
  engineWorkers: {},
  engineReport: '',
  engineReflection: null,
  engineStartedAt: null,
  engineElapsedSeconds: 0,
  enginePhase: null,
};

export const useDeepWorkStore = create<DeepWorkStore>()(
  persist(
    (set, get) => ({
  ...initialState,

  startSession: async (agentId, plan, message, conversationId) => {
    set({
      status: 'executing',
      conversationId: conversationId || null,
      plan,
      todos: plan.steps.map((s) => ({ text: s, status: 'pending' as const })),
      totalSteps: plan.steps.length,
      currentStep: 0,
      iterationCount: 0,
      error: null,
      steps: [],
      workspaceFiles: [],
      currentActivity: null,
      sources: [],
      connectors: [],
      summary: null,
      keyFindings: [],
      artifacts: [],
    });

    try {
      const { session_id, job_id } = await executeDeepWork({
        agent_id: agentId,
        plan,
        message,
        conversation_id: conversationId,
      });

      set({ sessionId: session_id, jobId: job_id });
      _tokenRefreshReconnects = 0;
      get().connectEvents(session_id);
    } catch (err) {
      set({
        status: 'error',
        error: err instanceof Error ? err.message : 'Failed to start session',
      });
    }
  },

  respondToQuestion: async (input) => {
    const { sessionId } = get();
    if (!sessionId) return;

    try {
      await sendHumanInput(sessionId, { input });
      set({ pendingQuestion: null, status: 'executing' });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to send input' });
    }
  },

  approvePlan: () => set({ status: 'executing' }),

  rejectPlan: () => set({ status: 'idle', plan: null, todos: [] }),

  pause: async () => {
    const { sessionId, status } = get();
    if (!sessionId) return;
    const prevStatus = status;
    set({ status: 'paused' });
    try {
      await pauseSession(sessionId);
    } catch (err) {
      set({ status: prevStatus, error: err instanceof Error ? err.message : 'Failed to pause' });
    }
  },

  resume: async () => {
    const { sessionId } = get();
    if (!sessionId) return;
    try {
      await resumeSession(sessionId);
      set({ status: 'executing' });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to resume' });
    }
  },

  stop: async () => {
    const { sessionId, status } = get();
    if (!sessionId) return;
    const prevStatus = status;
    set({ status: 'stopped' });
    try {
      await stopSession(sessionId);
      // SSE stays connected — deep_work_complete handler will naturally
      // upgrade to 'completed' if backend finishes gracefully
    } catch (err) {
      set({ status: prevStatus, error: err instanceof Error ? err.message : 'Failed to stop' });
    }
  },

  connectEvents: (sessionId) => {
    // Close existing connection
    get().disconnectEvents();

    const url = buildEventsUrl(sessionId);
    const es = new EventSource(url, { withCredentials: true });
    let retryCount = 0;

    es.addEventListener('deep_work_start', (e) => {
      try {
        const data = JSON.parse(e.data);
        set({
          status: 'executing',
          todos: data.todos || [],
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('step_complete', (e) => {
      retryCount = 0;
      try {
        const step = JSON.parse(e.data);
        set((state) => {
          const completedTodos = state.todos.filter((t) => t.status === 'completed').length;
          return {
            steps: [...state.steps, step],
            currentStep: completedTodos,
            totalSteps: step.total_steps || state.totalSteps,
            iterationCount: step.iteration || step.step_number || state.iterationCount + 1,
            currentActivity: null,
          };
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('step_activity', (e) => {
      retryCount = 0;
      try {
        const data = JSON.parse(e.data);
        set({ currentActivity: data.description || null });
      } catch { /* ignore */ }
    });

    es.addEventListener('todo_update', (e) => {
      retryCount = 0;
      try {
        const data = JSON.parse(e.data);
        const todos = data.todos || [];
        const completedCount = data.completed_count ?? todos.filter((t: { status: string }) => t.status === 'completed').length;
        set({
          todos,
          currentStep: completedCount,
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('workspace_update', (e) => {
      retryCount = 0;
      try {
        const data = JSON.parse(e.data);
        const file: WorkspaceFile = {
          path: data.path,
          action: data.action,
          size: data.size,
        };
        set((state) => {
          if (data.action === 'delete') {
            return {
              workspaceFiles: state.workspaceFiles.filter((f) => f.path !== data.path),
            };
          }
          const existing = state.workspaceFiles.findIndex((f) => f.path === data.path);
          if (existing >= 0) {
            const updated = [...state.workspaceFiles];
            updated[existing] = file;
            return { workspaceFiles: updated };
          }
          return { workspaceFiles: [...state.workspaceFiles, file] };
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('source_update', (e) => {
      retryCount = 0;
      try {
        const data = JSON.parse(e.data);
        set((state) => {
          const existingUrls = new Set(state.sources.map((s) => s.url));
          const newSources = (data.sources || []).filter((s: DeepWorkSource) => !existingUrls.has(s.url));
          return {
            sources: [...state.sources, ...newSources],
            connectors: data.connectors || state.connectors,
          };
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('human_input_required', (e) => {
      retryCount = 0;
      try {
        const data = JSON.parse(e.data);
        const pending: string | StructuredQuestion = data.options
          ? { question: data.question, options: data.options, multiSelect: data.multiSelect ?? false }
          : data.question;
        set({
          status: 'awaiting_input',
          pendingQuestion: pending,
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('deep_work_paused', () => {
      retryCount = 0;
      set({ status: 'paused' });
    });

    es.addEventListener('deep_work_complete', (e) => {
      retryCount = 0;
      try {
        const data = JSON.parse(e.data);
        const state = get();
        set({
          status: 'completed',
          currentStep: state.totalSteps,
          summary: data.summary,
          keyFindings: data.key_findings || [],
          artifacts: (data.artifacts || []).map((a: string) => ({
            type: 'file' as const,
            filename: a.split('/').pop() || a,
            artifactUri: a,
          })),
          elapsedSeconds: data.total_time || 0,
        });
        get().disconnectEvents();
      } catch { /* ignore */ }
    });

    es.addEventListener('deep_work_error', (e) => {
      retryCount = 0;
      try {
        const data = JSON.parse(e.data);
        set({
          status: 'error',
          error: data.error,
        });
        get().disconnectEvents();
      } catch { /* ignore */ }
    });

    es.onerror = () => {
      retryCount++;
      if (retryCount === 1 && _tokenRefreshReconnects < MAX_TOKEN_REFRESH_RECONNECTS) {
        // First error — likely JWT expiry. Refresh token, then reconnect with fresh cookies.
        import('@/lib/api').then(({ attemptTokenRefresh }) => {
          attemptTokenRefresh().then((success) => {
            if (success && get().eventSource === es) {
              _tokenRefreshReconnects++;
              es.close();
              set({ eventSource: null });
              get().connectEvents(sessionId);
            }
          }).catch(() => {});
        });
      } else if (retryCount >= 5) {
        // Reconcile from backend after repeated failures
        const convId = get().conversationId;
        if (convId) {
          import('@/lib/api/deep-work').then(({ getDeepWorkFullState }) => {
            getDeepWorkFullState(convId).then((fullState) => {
              if (fullState) {
                get().hydrateFromBackend(fullState);
                // If session is terminal, stop reconnecting
                const terminalStatuses = ['completed', 'failed', 'cancelled'];
                if (terminalStatuses.includes(fullState.status)) {
                  es.close();
                  set({ eventSource: null });
                  return;
                }
              }
              retryCount = 0;
            }).catch(() => { /* ignore — EventSource will keep retrying */ });
          });
        }
      }
    };

    es.onmessage = () => { retryCount = 0; };

    set({ eventSource: es });
  },

  disconnectEvents: () => {
    const { eventSource } = get();
    if (eventSource) {
      eventSource.close();
      set({ eventSource: null });
    }
  },

  hydrateFromBackend: (fullState: DeepWorkFullState) => {
    const mappedStatus: DeepWorkStatus =
      fullState.status === 'running' || fullState.status === 'executing' ? 'executing'
      : fullState.status === 'paused' ? 'paused'
      : fullState.status === 'awaiting_input' ? 'awaiting_input'
      : fullState.status === 'completed' ? 'completed'
      : fullState.status === 'failed' ? 'error'
      : fullState.status === 'cancelled' ? 'stopped'
      : fullState.status === 'pending' ? 'executing'
      : 'idle';

    const todos = fullState.todos || [];
    const completedCount = todos.filter((t) => t.status === 'completed').length;

    // Build plan from engine plan data if available
    let plan = fullState.plan ? { title: fullState.plan.title, steps: fullState.plan.steps } : null;
    if (fullState.is_engine && fullState.plan) {
      // Engine plan has sub_queries instead of steps
      const subQueries = (fullState.plan as Record<string, unknown>).sub_queries;
      if (Array.isArray(subQueries)) {
        const steps = subQueries.map((sq: Record<string, unknown>) =>
          typeof sq === 'string' ? sq : (sq.sub_query as string) || '',
        );
        plan = { title: fullState.plan.title || steps[0]?.split('?')[0] || 'Research', steps };
      }
    }

    // Rebuild engineWorkers from persisted workers list
    let engineWorkers: Record<string, EngineWorkerState> = {};
    if (fullState.workers && fullState.workers.length > 0) {
      for (const w of fullState.workers) {
        engineWorkers[w.worker_id] = {
          worker_id: w.worker_id,
          sub_query: w.sub_query,
          status: (w.status as EngineWorkerState['status']) || 'complete',
          learning_count: w.learning_count || 0,
          completionSummary: (w.source_count != null || w.duration_seconds != null)
            ? {
                findings: [],
                sourceCount: w.source_count || 0,
                duration: w.duration_seconds || 0,
                toolCallCount: 0,
              }
            : undefined,
        };
      }
    }

    set({
      sessionId: fullState.session_id,
      conversationId: fullState.conversation_id,
      status: mappedStatus,
      plan,
      todos,
      steps: (fullState.steps || []) as unknown as DeepWorkStep[],
      currentStep: completedCount,
      totalSteps: todos.length || plan?.steps.length || 0,
      workspaceFiles: (fullState.workspace_files || []).map((f) => ({
        path: f.path,
        action: 'create' as const,
        size: f.size,
      })),
      summary: fullState.summary,
      keyFindings: fullState.key_findings || [],
      sources: fullState.sources || [],
      connectors: fullState.connectors || [],
      artifacts: (fullState.artifacts || []).map((a) => ({
        type: 'file' as const,
        filename: a.filename || a.artifact_uri?.split('/').pop() || 'deliverable',
        artifactUri: a.artifact_uri,
        downloadUrl: a.download_url,
        size: a.size,
      })),
      error: fullState.error || null,
      // Engine-specific hydration
      engineMode: fullState.is_engine,
      engineReport: fullState.engine_report || '',
      engineWorkers,
      // Phase-aware hydration from persisted engine_state.json
      enginePhase: (fullState.engine_phase as EnginePhase) || null,
      // Clear stale activity/reflection state on hydration
      currentActivity: null,
      engineReflection: null,
    });
  },

  // ── Intake actions ─────────────────────────────────────────

  clarify: async (query, conversationId, availableKbs, availableConnectors) => {
    set({
      ...initialState,
      engineMode: true,
      status: 'clarifying',
      conversationId: conversationId || null,
      enginePendingQuery: query,
    });

    try {
      const result = await clarifyQuery(query, availableKbs || [], availableConnectors || []);
      if (result.action === 'proceed') {
        // Auto-skip: go straight to plan generation
        set({ intakeResult: null, status: 'planning' });
        const { title, steps, raw_plan } = await generateEnginePlan(query, result.enriched_query || undefined);
        set({
          status: 'awaiting_approval',
          plan: { title, steps },
          engineRawPlan: raw_plan,
        });
      } else {
        set({ intakeResult: result });
      }
    } catch (err) {
      // Graceful fallback: skip clarification, go to planning
      set({ intakeResult: null, status: 'planning' });
      try {
        const { title, steps, raw_plan } = await generateEnginePlan(query);
        set({
          status: 'awaiting_approval',
          plan: { title, steps },
          engineRawPlan: raw_plan,
        });
      } catch (planErr) {
        set({
          status: 'error',
          error: planErr instanceof Error ? planErr.message : 'Failed to generate plan',
        });
      }
    }
  },

  skipClarification: (enrichedQuery) => {
    const { enginePendingQuery, conversationId } = get();
    if (!enginePendingQuery) return;
    set({ intakeResult: null });
    // Fire-and-forget plan generation via the existing action
    get().generatePlan(enginePendingQuery, conversationId || undefined, enrichedQuery);
  },

  // ── Engine actions ──────────────────────────────────────────

  setDeliverable: (type, description) => {
    set((state) => ({
      engineRawPlan: {
        ...state.engineRawPlan,
        deliverable: type,
        deliverable_description: description || null,
      },
    }));
  },

  generatePlan: async (query, conversationId, enrichedQuery) => {
    set((prev) => ({
      ...initialState,
      engineMode: true,
      status: 'planning',
      conversationId: conversationId || prev.conversationId || null,
      enginePendingQuery: query,
    }));

    try {
      const { title, steps, raw_plan } = await generateEnginePlan(query, enrichedQuery);
      set({
        status: 'awaiting_approval',
        plan: { title, steps },
        engineRawPlan: raw_plan,
      });
    } catch (err) {
      set({
        status: 'error',
        error: err instanceof Error ? err.message : 'Failed to generate plan',
      });
    }
  },

  approveEnginePlan: async () => {
    const { conversationId, engineRawPlan, enginePendingQuery, plan } = get();
    if (!enginePendingQuery) return;

    // Pre-populate todos from plan steps so the checklist renders immediately
    const todos = (plan?.steps || []).map((s) => ({ text: s, status: 'pending' as const }));
    set({
      status: 'executing',
      engineStartedAt: Date.now(),
      todos,
      totalSteps: todos.length,
      // Clear stale state from previous sessions
      sources: [],
      connectors: [],
      engineWorkers: {},
      engineReport: '',
      engineReflection: null,
      enginePhase: null,
      currentActivity: null,
      currentStep: 0,
    });

    try {
      const { session_id } = await startEngine({
        query: enginePendingQuery,
        conversation_id: conversationId || undefined,
        plan: engineRawPlan || undefined,
      });
      set({ sessionId: session_id });
      _tokenRefreshReconnects = 0;
      get().connectEngineEvents(session_id);
    } catch (err) {
      set({
        status: 'error',
        error: err instanceof Error ? err.message : 'Failed to start engine',
      });
    }
  },

  rejectEnginePlan: () => {
    set({ ...initialState });
  },

  startEngineSession: async (query, conversationId) => {
    set({
      ...initialState,
      engineMode: true,
      status: 'executing',
      conversationId: conversationId || null,
      engineStartedAt: Date.now(),
    });

    try {
      const { session_id } = await startEngine({
        query,
        conversation_id: conversationId,
      });
      set({ sessionId: session_id });
      _tokenRefreshReconnects = 0;
      get().connectEngineEvents(session_id);
    } catch (err) {
      set({
        status: 'error',
        error: err instanceof Error ? err.message : 'Failed to start engine',
      });
    }
  },

  cancelEngineSession: async () => {
    const { sessionId } = get();
    if (!sessionId) return;
    try {
      await cancelEngine(sessionId);
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to cancel engine' });
    }
  },

  connectEngineEvents: (sessionId) => {
    get().disconnectEvents();

    const url = buildEngineEventsUrl(sessionId);
    const es = new EventSource(url, { withCredentials: true });

    es.addEventListener('deep_work_engine_planned', (e) => {
      try {
        const data = JSON.parse(e.data);
        const workers: Record<string, EngineWorkerState> = {};
        const todos: DeepWorkTodo[] = [];
        for (const sq of data.sub_queries) {
          const id = `w${Object.keys(workers).length + 1}`;
          workers[id] = { worker_id: id, sub_query: sq, status: 'idle', learning_count: 0 };
          todos.push({ text: sq, status: 'pending' });
        }
        set({
          enginePlan: { sub_queries: data.sub_queries },
          engineWorkers: workers,
          todos,
          totalSteps: todos.length,
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('deep_work_engine_gap_tasks', (e) => {
      try {
        const data = JSON.parse(e.data);
        set((state) => {
          const newWorkers = { ...state.engineWorkers };
          const newTodos = [...state.todos];
          for (const sq of data.sub_queries) {
            newWorkers[sq.worker_id] = {
              worker_id: sq.worker_id,
              sub_query: sq.sub_query,
              status: 'idle',
              learning_count: 0,
            };
            newTodos.push({ text: sq.sub_query, status: 'pending' as const });
          }
          return {
            engineWorkers: newWorkers,
            todos: newTodos,
            totalSteps: newTodos.length,
          };
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('deep_work_engine_worker_started', (e) => {
      try {
        const data = JSON.parse(e.data);
        set((state) => {
          // Only update status of existing todos — gap tasks are pre-registered by gap_tasks event
          const todos = state.todos.map((t) =>
            t.text === data.sub_query ? { ...t, status: 'in_progress' as const } : t,
          );
          const existing = state.engineWorkers[data.worker_id];
          return {
            engineWorkers: {
              ...state.engineWorkers,
              [data.worker_id]: {
                ...(existing || { learning_count: 0 }),
                worker_id: data.worker_id,
                sub_query: data.sub_query,
                status: 'researching' as const,
                activity: undefined,
                recentActions: undefined,
                error: undefined,
                completionSummary: undefined,
              },
            },
            todos,
            currentActivity: data.sub_query,
          };
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('deep_work_engine_worker_complete', (e) => {
      try {
        const data = JSON.parse(e.data);
        set((state) => {
          // Defensive guard: if worker_id unknown, create minimal entry
          const existing = state.engineWorkers[data.worker_id];
          const workerSubQuery = existing?.sub_query;
          const todos = state.todos.map((t) =>
            t.text === workerSubQuery ? { ...t, status: 'completed' as const } : t,
          );
          const completedCount = todos.filter((t) => t.status === 'completed').length;
          const workerUpdate: EngineWorkerState = {
            ...(existing || { worker_id: data.worker_id, sub_query: '', learning_count: 0 }),
            status: data.error ? 'error' as const : 'complete' as const,
            learning_count: data.learning_count,
            error: data.error || null,
            activity: undefined,
            recentActions: undefined,
            completionSummary: {
              findings: data.top_findings || [],
              sourceCount: data.source_count || 0,
              duration: data.duration_seconds || 0,
              toolCallCount: data.tool_call_count || 0,
            },
          };
          const updates: Partial<DeepWorkState> = {
            engineWorkers: {
              ...state.engineWorkers,
              [data.worker_id]: workerUpdate,
            },
            todos,
            currentStep: completedCount,
          };
          // Clear stale activity when all workers in this round are done
          if (completedCount === todos.length) {
            updates.currentActivity = null;
          }
          return updates;
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('source_update', (e) => {
      try {
        const data = JSON.parse(e.data);
        set((state) => {
          const existingUrls = new Set(state.sources.map((s) => s.url));
          const newSources = (data.sources || []).filter((s: DeepWorkSource) => !existingUrls.has(s.url));
          return {
            sources: [...state.sources, ...newSources],
          };
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('workspace_update', (e) => {
      try {
        const data = JSON.parse(e.data);
        const file: WorkspaceFile = {
          path: data.path,
          action: data.action,
          size: data.size,
        };
        set((state) => {
          if (data.action === 'delete') {
            return {
              workspaceFiles: state.workspaceFiles.filter((f) => f.path !== data.path),
            };
          }
          const existing = state.workspaceFiles.findIndex((f) => f.path === data.path);
          if (existing >= 0) {
            const updated = [...state.workspaceFiles];
            updated[existing] = file;
            return { workspaceFiles: updated };
          }
          return { workspaceFiles: [...state.workspaceFiles, file] };
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('deep_work_engine_worker_activity', (e) => {
      try {
        const data = JSON.parse(e.data);
        set((state) => {
          // Defensive guard: if worker_id unknown, create minimal entry
          const existing = state.engineWorkers[data.worker_id];
          const worker: EngineWorkerState = existing || {
            worker_id: data.worker_id,
            sub_query: '',
            status: 'researching',
            learning_count: 0,
          };
          return {
            engineWorkers: {
              ...state.engineWorkers,
              [data.worker_id]: {
                ...worker,
                activity: data.description || undefined,
                recentActions: data.recent_actions || worker.recentActions,
              },
            },
            currentActivity: data.description || null,
          };
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('sandbox_screenshot', (e) => {
      try {
        const data = JSON.parse(e.data);
        set((state) => {
          const existing = state.engineWorkers[data.worker_id];
          if (!existing) return state;
          return {
            engineWorkers: {
              ...state.engineWorkers,
              [data.worker_id]: {
                ...existing,
                lastScreenshot: {
                  url: data.url,
                  pageUrl: data.page_url || '',
                  timestamp: data.timestamp || Date.now(),
                },
              },
            },
          };
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('deep_work_engine_reflection', (e) => {
      try {
        const data = JSON.parse(e.data);
        set({
          engineReflection: {
            score: data.score,
            gaps: data.gaps,
            round: data.round,
            continuing: data.continuing,
          },
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('deep_work_engine_phase', (e) => {
      try {
        const data = JSON.parse(e.data);
        set({
          enginePhase: data.phase,
          // Clear worker-level activity on phase transitions (not during researching)
          ...(data.phase !== 'researching' ? { currentActivity: null } : {}),
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('deep_work_engine_synthesis_chunk', (e) => {
      try {
        const data = JSON.parse(e.data);
        set((state) => ({
          engineReport: state.engineReport + (data.content || ''),
        }));
      } catch { /* ignore */ }
    });

    // ── Output phase SSE handlers ──────────────────────────────
    es.addEventListener('deep_work_engine_output_started', (e) => {
      try {
        const data = JSON.parse(e.data);
        set({
          currentActivity: `Generating ${data.deliverable_type?.replace('_', ' ') || 'deliverable'}...`,
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('deep_work_engine_output_progress', (e) => {
      try {
        const data = JSON.parse(e.data);
        set({
          currentActivity: data.description || `Output iteration ${data.iteration}`,
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('deep_work_engine_output_validation', (e) => {
      try {
        const data = JSON.parse(e.data);
        const status = data.passed ? 'Validation passed' : `Issues found: ${(data.issues || []).join(', ')}`;
        set({
          currentActivity: `Iteration ${data.iteration}: ${status}`,
        });
      } catch { /* ignore */ }
    });

    es.addEventListener('deep_work_engine_output_complete', (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.file_part) {
          set((state) => ({
            artifacts: [...state.artifacts, data.file_part as DeepWorkArtifact],
            currentActivity: null,
          }));
        } else if (data.artifact_uri) {
          set((state) => ({
            artifacts: [...state.artifacts, {
              type: 'file' as const,
              filename: data.artifact_uri.split('/').pop() || 'deliverable',
              artifactUri: data.artifact_uri,
              downloadUrl: data.download_url,
            }],
            currentActivity: null,
          }));
        } else {
          set({ currentActivity: null });
        }
      } catch { /* ignore */ }
    });

    es.addEventListener('deep_work_engine_complete', () => {
      const state = get();
      set({
        status: 'completed',
        enginePhase: null,
        engineReflection: null,
        // Bridge engine report into summary so useDeepWorkChat result-saving fires
        summary: state.engineReport || null,
        engineElapsedSeconds: state.engineStartedAt
          ? Math.floor((Date.now() - state.engineStartedAt) / 1000)
          : 0,
      });
      get().disconnectEvents();
    });

    es.addEventListener('deep_work_engine_error', (e) => {
      try {
        const data = JSON.parse(e.data);
        set({ status: 'error', error: data.message });
      } catch {
        set({ status: 'error', error: 'Engine error' });
      }
      get().disconnectEvents();
    });

    let retryCount = 0;
    const resetRetry = () => { retryCount = 0; };
    es.onmessage = resetRetry;

    es.onerror = () => {
      retryCount++;
      if (retryCount === 1 && _tokenRefreshReconnects < MAX_TOKEN_REFRESH_RECONNECTS) {
        // First error — likely JWT expiry. Refresh token, then reconnect with fresh cookies.
        import('@/lib/api').then(({ attemptTokenRefresh }) => {
          attemptTokenRefresh().then((success) => {
            if (success && get().eventSource === es) {
              _tokenRefreshReconnects++;
              es.close();
              set({ eventSource: null });
              get().connectEngineEvents(sessionId);
            }
          }).catch(() => {});
        });
      } else if (retryCount >= 5) {
        // Reconcile from backend after repeated failures
        const convId = get().conversationId;
        if (convId) {
          import('@/lib/api/deep-work').then(({ getDeepWorkFullState }) => {
            getDeepWorkFullState(convId).then((fullState) => {
              if (fullState) {
                get().hydrateFromBackend(fullState);
                // If session is terminal, stop reconnecting
                const terminalStatuses = ['completed', 'failed', 'cancelled'];
                if (terminalStatuses.includes(fullState.status)) {
                  es.close();
                  set({ eventSource: null });
                  return;
                }
              }
              retryCount = 0;
            }).catch(() => { /* ignore — EventSource will keep retrying */ });
          });
        }
      }
    };

    set({ eventSource: es });
  },

  reset: () => {
    get().disconnectEvents();
    _tokenRefreshReconnects = 0;
    set(initialState);
  },
}),
    {
      name: 'fi-deep-work',
      partialize: (state) => {
        // Only persist pre-execution fields
        const preExecStatuses: DeepWorkStatus[] = ['clarifying', 'planning', 'awaiting_approval'];
        if (!preExecStatuses.includes(state.status)) {
          // For executing/completed/error/idle/stopped — persist minimal marker only
          return {
            status: state.status,
            conversationId: state.conversationId,
            engineMode: state.engineMode,
            todos: state.todos,
            persistedAt: Date.now(),
          };
        }
        // Full pre-execution state
        return {
          status: state.status,
          conversationId: state.conversationId,
          engineMode: state.engineMode,
          enginePendingQuery: state.enginePendingQuery,
          intakeResult: state.intakeResult,
          plan: state.plan,
          engineRawPlan: state.engineRawPlan,
          error: state.error,
          persistedAt: Date.now(),
        };
      },
      merge: (persisted, current) => {
        const merged = { ...current, ...(persisted as Partial<DeepWorkStore>) };
        // Expire state older than 24 hours
        const age = Date.now() - (merged.persistedAt || 0);
        if (age > 24 * 60 * 60 * 1000) {
          return { ...current, ...initialState };
        }
        return merged;
      },
    },
  ),
);

// ── Selectors ───────────────────────────────────────────────
export const selectIsDeepWorkActive = (state: DeepWorkStore) =>
  state.status !== 'idle' && state.status !== 'completed' && state.status !== 'error' && state.status !== 'stopped';

export const selectCompletedTodoCount = (state: DeepWorkStore) =>
  state.todos.filter((t) => t.status === 'completed').length;

export const selectProgress = (state: DeepWorkStore) => {
  if (state.totalSteps === 0) return 0;
  return Math.min(100, Math.round((state.currentStep / state.totalSteps) * 100));
};

export const selectConversationId = (state: DeepWorkStore) => state.conversationId;

export const selectWorkspaceFiles = (state: DeepWorkStore) => state.workspaceFiles;

export const selectCurrentActivity = (state: DeepWorkStore) => state.currentActivity;

export const selectSources = (state: DeepWorkStore) => state.sources;

export const selectConnectors = (state: DeepWorkStore) => state.connectors;

// ── Engine selectors ────────────────────────────────────────
export const selectEngineMode = (state: DeepWorkStore) => state.engineMode;

export const selectEnginePlan = (state: DeepWorkStore) => state.enginePlan;

export const selectEngineWorkers = (state: DeepWorkStore) => state.engineWorkers;

export const selectEngineReport = (state: DeepWorkStore) => state.engineReport;

export const selectEngineReflection = (state: DeepWorkStore) => state.engineReflection;

export const selectEnginePhase = (state: DeepWorkStore) => state.enginePhase;

export const selectArtifacts = (state: DeepWorkStore) => state.artifacts;
