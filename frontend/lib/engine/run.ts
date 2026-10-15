import type { ConversationView } from './types';

/**
 * Whether the conversation has an active run. The engine's `run` member stays
 * true through tool execution and approval waits, when `live` is already
 * cleared; older engines lack it, so fall back to the streaming `live` run.
 */
export function isRunActive(view: ConversationView | null | undefined): boolean {
  if (!view) return false;
  return view.run?.active ?? view.live.runId != null;
}

/** The active run's id, when the engine reports one. */
export function activeRunId(view: ConversationView | null | undefined): string | null {
  if (!view) return null;
  return view.run ? (view.run.active ? (view.run.runId ?? view.live.runId ?? 'active') : null) : (view.live.runId ?? null);
}
