import type { ConversationView } from './types';

/**
 * Whether a compaction is running, read from the view.
 *
 * GAP: the engine view (engine/src/watch/view.ts) does not include the
 * `agent.compaction` document or compaction tasks, so a running compaction is
 * not visible to clients today. This reads an optional `compaction.running`
 * field so the transient state lights up as soon as the view exposes one; until
 * then it is always false and the marker appears when the compaction entry lands.
 */
export function isCompactionRunning(view: ConversationView | null): boolean {
  const c = (view as unknown as { compaction?: { running?: unknown } } | null)?.compaction;
  return c != null && c.running != null && c.running !== false;
}
