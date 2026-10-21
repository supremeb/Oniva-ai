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
import {
  startEngine,
  cancelEngine,
  buildEngineEventsUrl,
  generateEnginePlan,
  clarifyQuery,
} from '@/lib/api/deep-work';
import { APIError } from '@/lib/api';
import { clarifyEngine, planEngine, startEngineRun } from '@/lib/engine/deepWorkApi';
import { useEngineSessionStore } from '@/components/deep-work/engineSession';

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
  /** Explicit discriminator from the backend; absent on older responses. */
  runtime?: 'engine' | 'legacy';
  engine_report?: string | null;
  engine_phase: string | null;
  /** Engine intake context (engine runs only): restores a plan or questions still waiting on the user. */
  query?: string | null;
  enriched_query?: string | null;
  project_id?: string | null;
  awaiting_input?: { action?: string; questions?: IntakeResult['questions'] } | null;
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

  // ── Engine runtime intake (flag ENABLE_ENGINE_DEEP_WORK) ───
  /** Set while clarify/plan/start run on the engine API; null on the legacy flow. */
  engineIntake: EngineIntakeContext | null;
  /** Plan 422 after the server's one repair: the model's raw text, shown so the user can retry. */
  planFailure: { message: string; text: string | null } | null;
}

/** What the engine intake carries from clarify through plan to start. */
export interface EngineIntakeContext {
  projectId: string;
  /** deep_work_runs.id, minted by the first clarify/plan call. */
  sessionId: string | null;
  /** The run's own engine conversation. */
  conversationId: string | null;
  kbIds: string[];
  connectorIds: string[];
  enrichedQuery: string | null;
}

export interface EngineIntakeOptions {
  /** Present when the tenant's Deep Work runs on the engine; `projectId` is the chat's project. */
  engine?: { projectId: string };
}

interface DeepWorkActions {
  disconnectEvents: () => void;
  /** Hydrate store from backend full-state response */
  hydrateFromBackend: (fullState: DeepWorkFullState) => void;
  /**
   * Restore an engine intake that is still waiting on the user (questions asked, or a plan to approve) after the
   * page was left. True when it restored one; false when the run is past the intake (the run card shows it).
   */
  restoreEngineIntake: (fullState: DeepWorkFullState, chatId: string, projectId?: string | null) => boolean;
  reset: () => void;
  // ── Intake actions ─────────────────────────────────────────
  clarify: (query: string, conversationId?: string, availableKbs?: string[], availableConnectors?: string[], opts?: EngineIntakeOptions) => Promise<void>;
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
  engineIntake: null,
  planFailure: null,
};

/** 422 from engine plan: `{detail: {message, text, error}}` after the server's repair. */
function planFailureOf(err: unknown): { message: string; text: string | null } | null {
  if (!(err instanceof APIError) || err.status !== 422) return null;
  const detail = (err.data as { detail?: unknown } | undefined)?.detail;
  if (detail && typeof detail === 'object') {
    const d = detail as { message?: unknown; text?: unknown };
    return {
      message: typeof d.message === 'string' ? d.message : 'The plan could not be parsed',
      text: typeof d.text === 'string' && d.text ? d.text : null,
    };
  }
  return { message: typeof detail === 'string' ? detail : err.message, text: null };
}

export const useDeepWorkStore = create<DeepWorkStore>()(
  persist(
    (set, get) => ({
  ...initialState,

  disconnectEvents: () => {
    const { eventSource } = get();
    if (eventSource) {
      eventSource.close();
      set({ eventSource: null });
    }
  },

  hydrateFromBackend: (fullState: DeepWorkFullState) => {
    // A non-engine (v1) session can no longer be running: its executor is gone, so a
    // saved non-terminal status is shown as stopped and the session is read-only history.
    const inFlight = ['running', 'executing', 'pending', 'paused', 'awaiting_input'].includes(fullState.status);
    const mappedStatus: DeepWorkStatus =
      inFlight ? (fullState.is_engine ? 'executing' : 'stopped')
      : fullState.status === 'completed' ? 'completed'
      : fullState.status === 'failed' ? 'error'
      : fullState.status === 'cancelled' ? 'stopped'
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

  restoreEngineIntake: (fullState, chatId, projectId) => {
    const query = fullState.query;
    const phase = fullState.engine_phase;
    if (fullState.status !== 'running' || !query || (phase !== 'clarify' && phase !== 'plan')) return false;
    const intake: EngineIntakeContext = {
      projectId: fullState.project_id ?? projectId ?? '',
      sessionId: fullState.session_id,
      conversationId: fullState.conversation_id,
      kbIds: [],
      connectorIds: [],
      enrichedQuery: fullState.enriched_query ?? null,
    };
    const base = { ...initialState, engineMode: true, conversationId: chatId, enginePendingQuery: query, engineIntake: intake };
    const asked = fullState.awaiting_input;
    if (asked?.action === 'ask' && asked.questions?.length) {
      set({ ...base, status: 'clarifying', intakeResult: { action: 'ask', questions: asked.questions, enriched_query: null } });
      return true;
    }
    const raw = fullState.plan as unknown as { title?: string; sub_queries?: Array<string | { sub_query?: string }> } | null;
    if (phase === 'plan' && raw && Array.isArray(raw.sub_queries) && raw.sub_queries.length > 0) {
      const steps = raw.sub_queries.map((q) => (typeof q === 'string' ? q : q.sub_query ?? '')).filter(Boolean);
      set({
        ...base,
        status: 'awaiting_approval',
        plan: { title: raw.title ?? 'Research plan', steps },
        engineRawPlan: raw as Record<string, unknown>,
      });
      return true;
    }
    return false;
  },

  // ── Intake actions ─────────────────────────────────────────

  clarify: async (query, conversationId, availableKbs, availableConnectors, opts) => {
    // A new intake replaces whatever engine run card the chat was showing.
    useEngineSessionStore.getState().setSession(null);
    const engine = opts?.engine;
    set({
      ...initialState,
      engineMode: true,
      status: 'clarifying',
      conversationId: conversationId || null,
      enginePendingQuery: query,
      engineIntake: engine
        ? {
            projectId: engine.projectId,
            sessionId: null,
            conversationId: null,
            kbIds: availableKbs || [],
            connectorIds: availableConnectors || [],
            enrichedQuery: null,
          }
        : null,
    });

    if (engine) {
      try {
        const result = await clarifyEngine({
          query,
          projectId: engine.projectId,
          availableKbs: availableKbs || [],
          availableConnectors: availableConnectors || [],
          chatId: conversationId,
        });
        set((s) => ({
          engineIntake: s.engineIntake && {
            ...s.engineIntake,
            sessionId: result.session_id,
            conversationId: result.conversation_id,
          },
        }));
        if (result.action === 'proceed') {
          await get().generatePlan(query, conversationId, result.enriched_query || undefined);
        } else {
          set({
            intakeResult: {
              action: 'ask',
              questions: result.questions ?? null,
              enriched_query: result.enriched_query ?? null,
            },
          });
        }
      } catch {
        // Same graceful fallback as legacy: skip clarification and plan (plan mints the session itself).
        await get().generatePlan(query, conversationId);
      }
      return;
    }

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
    const intake = get().engineIntake;
    set((prev) => ({
      ...initialState,
      engineMode: true,
      status: 'planning',
      conversationId: conversationId || prev.conversationId || null,
      enginePendingQuery: query,
      engineIntake: intake && { ...intake, enrichedQuery: enrichedQuery ?? intake.enrichedQuery },
    }));

    if (intake) {
      const enriched = enrichedQuery ?? intake.enrichedQuery ?? undefined;
      try {
        const res = await planEngine({
          query,
          enrichedQuery: enriched,
          sessionId: intake.sessionId ?? undefined,
          conversationId: intake.conversationId ?? undefined,
          projectId: intake.projectId,
          chatId: get().conversationId ?? undefined,
        });
        set((s) => ({
          status: 'awaiting_approval',
          plan: { title: res.title, steps: res.steps },
          engineRawPlan: res.raw_plan,
          engineIntake: s.engineIntake && {
            ...s.engineIntake,
            sessionId: res.session_id,
            conversationId: res.conversation_id,
          },
        }));
      } catch (err) {
        const failure = planFailureOf(err);
        set({
          status: 'error',
          error: failure ? failure.message : err instanceof Error ? err.message : 'Failed to generate plan',
          planFailure: failure,
        });
      }
      return;
    }

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
    const { conversationId, engineRawPlan, enginePendingQuery, plan, engineIntake } = get();
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

    if (engineIntake) {
      if (!engineIntake.sessionId) {
        set({ status: 'error', error: 'Deep Work session is missing; please try again.' });
        return;
      }
      try {
        const res = await startEngineRun({
          sessionId: engineIntake.sessionId,
          query: enginePendingQuery,
          plan: engineRawPlan || undefined,
          enrichedQuery: engineIntake.enrichedQuery ?? undefined,
          knowledgeBaseIds: engineIntake.kbIds.length ? engineIntake.kbIds : undefined,
          connectorIds: engineIntake.connectorIds.length ? engineIntake.connectorIds : undefined,
          chatId: conversationId ?? undefined,
        });
        // Hand over to the engine card (it reads the run from its own watch stream), then
        // clear the intake state so the legacy card and progress bar stay out of the way.
        useEngineSessionStore.getState().setSession({
          sessionId: res.session_id,
          conversationId: res.conversation_id,
          chatId: conversationId ?? res.conversation_id,
          status: 'running',
          error: null,
          planTitle: plan?.title ?? null,
        });
        set({ ...initialState });
      } catch (err) {
        set({
          status: 'error',
          error: err instanceof Error ? err.message : 'Failed to start Deep Work',
        });
      }
      return;
    }

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
          engineIntake: state.engineIntake,
          planFailure: state.planFailure,
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
