'use client';

import type { InboxState } from '@/lib/engine';

export interface SendingChip {
  requestId: string;
  mode: 'steer' | 'followUp';
  text?: string;
}

export interface QueuedChipsProps {
  inbox: InboxState | null;
  /** Busy-mode submissions whose POST has not been acknowledged yet. */
  sending?: SendingChip[];
}

const MODE_LABEL = { steer: 'Steer', followUp: 'Follow-up' } as const;

interface Chip {
  key: string;
  mode: 'steer' | 'followUp';
  state: 'sending' | 'queued' | 'consumed';
  text?: string;
}

/**
 * Inputs sent during a run (R15), from the server inbox so a refresh keeps them.
 * Time order: placed steers (already delivered), then the queue in arrival order,
 * then sends still in flight. The view carries no text for inbox items and no
 * cancel route exists yet, so chips show mode and state only.
 */
export function QueuedChips({ inbox, sending = [] }: QueuedChipsProps) {
  const chips: Chip[] = [
    ...(inbox?.placed ?? []).map((p): Chip => ({ key: `placed:${p.submissionId}`, mode: 'steer', state: 'consumed' })),
    ...(inbox?.queue ?? []).map((q): Chip => ({ key: `queue:${q.submissionId}`, mode: q.mode, state: 'queued' })),
    ...sending.map((s): Chip => ({ key: `sending:${s.requestId}`, mode: s.mode, state: 'sending', text: s.text })),
  ];
  if (chips.length === 0) return null;
  return (
    <ul aria-label="Queued messages" className="mb-2 flex flex-wrap gap-1.5 px-1" data-testid="queued-chips">
      {chips.map((c) => (
        <li
          key={c.key}
          data-state={c.state}
          data-mode={c.mode}
          className="flex max-w-full items-center gap-1.5 rounded-full border border-neutral-200 bg-neutral-50 px-2.5 py-1 text-xs text-neutral-700"
        >
          <span className="font-medium">{MODE_LABEL[c.mode]}</span>
          {c.text && <span className="truncate max-w-[200px] text-neutral-500">{c.text}</span>}
          <span className="text-neutral-400">{c.state}</span>
        </li>
      ))}
    </ul>
  );
}
