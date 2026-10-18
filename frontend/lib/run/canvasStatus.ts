/** Canvas node styling from the shared run status (plan 003, U26). `undefined` and `queued` draw nothing. */
import { runStatusLabel, type RunStatus } from './status';

type Style = { ring: string; dot: string };

const STYLE: Partial<Record<RunStatus, Style>> = {
  running: { ring: 'ring-2 ring-blue-500 ring-offset-2 shadow-[0_0_20px_rgba(59,130,246,0.4)] animate-pulse motion-reduce:animate-none', dot: 'bg-blue-500 animate-pulse motion-reduce:animate-none' },
  resuming: { ring: 'ring-2 ring-blue-400 ring-offset-2 shadow-[0_0_16px_rgba(96,165,250,0.4)]', dot: 'bg-blue-400' },
  waiting: { ring: 'ring-2 ring-amber-500 ring-offset-1 shadow-[0_0_12px_rgba(245,158,11,0.35)]', dot: 'bg-amber-500' },
  completed: { ring: 'ring-2 ring-green-500 ring-offset-1 shadow-[0_0_12px_rgba(34,197,94,0.3)]', dot: 'bg-green-500' },
  failed: { ring: 'ring-2 ring-red-500 ring-offset-1 shadow-[0_0_12px_rgba(239,68,68,0.3)]', dot: 'bg-red-500' },
  cancelled: { ring: 'ring-2 ring-neutral-400 ring-offset-1', dot: 'bg-neutral-400' },
  skipped: { ring: 'ring-1 ring-neutral-300 ring-offset-1 opacity-60', dot: 'bg-neutral-300' },
  interrupted: { ring: 'ring-2 ring-orange-500 ring-offset-1', dot: 'bg-orange-500' },
};

/** Ring classes for a node. */
export function canvasRingClass(status: RunStatus | undefined): string {
  return (status && STYLE[status]?.ring) || '';
}

export function canvasDotClass(status: RunStatus | undefined): string | null {
  return (status && STYLE[status]?.dot) || null;
}

export function canvasStatusLabel(status: RunStatus | undefined): string | null {
  return status && status !== 'queued' ? runStatusLabel(status) : null;
}

/** The path leading out of a node is drawn as done once it completed or was skipped over. */
export function canvasEdgeDone(status: RunStatus | undefined): boolean {
  return status === 'completed';
}

/** The canvas used `pending | running | complete | error` before the shared enum. */
export function fromLegacyCanvasStatus(s: string): RunStatus {
  switch (s) {
    case 'complete':
      return 'completed';
    case 'error':
      return 'failed';
    case 'pending':
      return 'queued';
    default:
      return s as RunStatus;
  }
}
