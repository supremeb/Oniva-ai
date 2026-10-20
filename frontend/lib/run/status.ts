/**
 * One run status enum (plan 003, KTD10). The legacy mapping lives here and in
 * backend/app/services/run_status.py; both are asserted against
 * backend/tests/fixtures/run_status_map.json.
 */
export const RUN_STATUSES = [
  'queued',
  'running',
  'waiting',
  'resuming',
  'completed',
  'failed',
  'cancelled',
  'skipped',
  'interrupted',
] as const;

export type RunStatus = (typeof RUN_STATUSES)[number];

const LEGACY: Record<string, RunStatus> = {
  pending: 'queued',
  queued: 'queued',
  running: 'running',
  waiting: 'waiting',
  resuming: 'resuming',
  completed: 'completed',
  success: 'completed',
  failed: 'failed',
  error: 'failed',
  cancelled: 'cancelled',
  skipped: 'skipped',
  interrupted: 'interrupted',
};

/** Engine task states (`TaskView.status`) and terminal outcomes. */
const ENGINE_TASK: Record<string, RunStatus> = {
  pending: 'queued',
  running: 'running',
  completing: 'running',
  waiting: 'waiting',
  done: 'completed',
  aborted: 'cancelled',
  faulted: 'failed',
};

/** Map a legacy job or execution status. Unknown values become `failed` (logged). */
export function normalizeStatus(value: unknown, source = ''): RunStatus {
  const mapped = typeof value === 'string' ? LEGACY[value.trim().toLowerCase()] : undefined;
  if (!mapped) {
    console.warn(`unknown run status ${JSON.stringify(value)} from ${source || 'unknown source'}`);
    return 'failed';
  }
  return mapped;
}

/** Map an engine task state or terminal outcome. Unknown values become `failed` (logged). */
export function engineTaskStatus(status: unknown): RunStatus {
  const mapped = typeof status === 'string' ? ENGINE_TASK[status] : undefined;
  if (!mapped) {
    console.warn(`unknown engine task status ${JSON.stringify(status)}`);
    return 'failed';
  }
  return mapped;
}

const TERMINAL: ReadonlySet<RunStatus> = new Set(['completed', 'failed', 'cancelled', 'skipped', 'interrupted']);

/** Finished: nothing more will happen to this node. `interrupted` is final (its outcome is unknown). */
export const isTerminal = (s: RunStatus): boolean => TERMINAL.has(s);
/** Still in progress (including waiting and being resumed). */
export const isActive = (s: RunStatus): boolean => !TERMINAL.has(s);

const LABELS: Record<RunStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  waiting: 'Waiting',
  resuming: 'Resuming',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
  skipped: 'Skipped',
  interrupted: 'Interrupted',
};

export const runStatusLabel = (s: RunStatus): string => LABELS[s];
