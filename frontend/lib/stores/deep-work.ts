/**
 * Deep Work Zustand store — manages autonomous execution state.
 *
 * Pre-execution state (clarifying, planning, awaiting_approval) is persisted
 * to localStorage via Zustand persist middleware so it survives page refresh.
 * Execution state is durable on the backend — on chat open, the frontend
 * fetches full state from GET /deep-work/by-conversation/{id}/full-state
 * and hydrates via hydrateFromBackend(). Runs execute on the engine runtime and
 * are rendered by the engine card from its own watch stream; this store holds the
 * intake (clarify -> plan -> approve) and read-only history of earlier sessions.
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
import { APIError } from '@/lib/api';
import { clarifyEngine, planEngine, startEngineRun } from '@/lib/engine/deepWorkApi';
import { useEngineSessionStore } from '@/components/deep-work/engineSession';

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
  /** Carries the intake through clarify, plan and start on the engine API. */
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
  /** Hydrate store from backend full-state response */
  hydrateFromBackend: (fullState: DeepWorkFullState) => void;
  reset: () => void;
  // ── Intake actions ─────────────────────────────────────────
  clarify: (query: string, conversationId?: string, availableKbs?: string[], availableConnectors?: string[], opts?: EngineIntakeOptions) => Promise<void>;
  skipClarification: (enrichedQuery?: string) => void;
  // ── Engine actions ──────────────────────────────────────────
  setDeliverable: (type: string | null, description?: string) => void;
  generatePlan: (query: string, conversationId?: string, enrichedQuery?: string) => Promise<void>;
  approveEnginePlan: () => Promise<void>;
  rejectEnginePlan: () => void;
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

const DEEP_WORK_UNAVAILABLE = 'Deep Work is unavailable for this workspace.';

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

  hydrateFromBackend: (fullState: DeepWorkFullState) => {
    // Sessions hydrated here are historical (v1 and v2): their executors are gone, so a saved
    // non-terminal status is shown as stopped and the session is read-only history.
    const inFlight = ['running', 'executing', 'pending', 'paused', 'awaiting_input'].includes(fullState.status);
    const mappedStatus: DeepWorkStatus =
      inFlight ? 'stopped'
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

    set({ status: 'error', error: DEEP_WORK_UNAVAILABLE });
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

    set({ status: 'error', error: DEEP_WORK_UNAVAILABLE });
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

    set({ status: 'error', error: DEEP_WORK_UNAVAILABLE });
  },

  rejectEnginePlan: () => {
    set({ ...initialState });
  },

  reset: () => {
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
