import { create } from 'zustand';
import type { DeepWorkFullState } from '@/lib/stores/deep-work';

/**
 * The Deep Work session an engine-backed chat is showing. Engine sessions never
 * hydrate the legacy store (no SSE, no hydrateFromBackend): the rejoin hook
 * publishes the ids here and `DeepWorkCard` mounts the engine card from them.
 */
export interface EngineDeepWorkSession {
  /** deep_work_runs.id */
  sessionId: string;
  conversationId: string;
  /** Chat the session belongs to (the by-conversation key). */
  chatId: string;
  /** Backend status at fetch time; the live view wins once it arrives. */
  status: string | null;
  error: string | null;
  planTitle: string | null;
}

export type EngineTerminal = 'completed' | 'error' | 'cancelled';

interface EngineSessionStore {
  session: EngineDeepWorkSession | null;
  /** Set by the card when it watched the run end (never on a rejoin of a finished run). */
  terminal: { sessionId: string; status: EngineTerminal } | null;
  setSession(session: EngineDeepWorkSession | null): void;
  setTerminal(sessionId: string, status: EngineTerminal): void;
}

export const useEngineSessionStore = create<EngineSessionStore>((set) => ({
  session: null,
  terminal: null,
  setSession: (session) => set({ session, terminal: null }),
  setTerminal: (sessionId, status) => set({ terminal: { sessionId, status } }),
}));

/**
 * Full-state of an engine-runtime run. The backend sets `runtime: "engine"` explicitly
 * (legacy responses say `"legacy"`). Older responses have no `runtime`; for those only,
 * fall back to the old heuristic: the legacy v2 path also reports `is_engine: true` but
 * carries an `engine_report` key, which the engine runtime's state lacks.
 */
export function isEngineRuntimeSession(fs: DeepWorkFullState | null | undefined): fs is DeepWorkFullState & {
  conversation_id: string;
} {
  if (!fs || fs.is_engine !== true || !fs.conversation_id) return false;
  if (fs.runtime) return fs.runtime === 'engine';
  return !Object.prototype.hasOwnProperty.call(fs, 'engine_report');
}
