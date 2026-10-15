'use client';

import { useState } from 'react';
import type { InboxState } from '@/lib/engine';

export interface SendingChip {
  requestId: string;
  mode: 'steer' | 'followUp';
  text?: string;
  /** The send failed for good (retries exhausted or refused): shown as rejected. */
  rejected?: boolean;
}

export interface QueuedChipsProps {
  inbox: InboxState | null;
  /** Busy-mode submissions whose POST has not been acknowledged yet. */
  sending?: SendingChip[];
  /**
   * Withdraw a queued item (`useEngineConversation().cancelSubmission`). Without
   * it, or against an engine that sends no `preview`, queued chips have no x.
   */
  onCancel?: (submissionId: string) => Promise<void> | void;
}

const MODE_LABEL = { steer: 'Steer', followUp: 'Follow-up' } as const;

type ChipState = 'sending' | 'queued' | 'consumed' | 'rejected' | 'withdrawn';

interface Chip {
  key: string;
  mode: 'steer' | 'followUp';
  state: ChipState;
  text?: string;
  /** Set on queued chips that can be cancelled. */
  submissionId?: string;
}

/**
 * Inputs sent during a run (R15), from the server inbox so a refresh keeps them.
 * Time order: placed steers (already delivered), then the queue in arrival order,
 * then sends still in flight. Queued chips show the engine's text preview (when
 * it sends one) and an x that withdraws the input; once the engine settles it
 * `withdrawn` the chip drops out of the inbox, and until then it reads
 * "withdrawn". A cancel that loses the race (409: already started) just
 * leaves the chip to move on with the inbox.
 */
export function QueuedChips({ inbox, sending = [], onCancel }: QueuedChipsProps) {
  const [withdrawn, setWithdrawn] = useState<ReadonlySet<string>>(new Set());
  const [cancelling, setCancelling] = useState<ReadonlySet<string>>(new Set());

  const cancel = async (id: string) => {
    if (!onCancel) return;
    setCancelling((s) => new Set(s).add(id));
    try {
      await onCancel(id);
      setWithdrawn((s) => new Set(s).add(id));
    } catch {
      // Already started or the call failed: the inbox patch shows the truth.
    } finally {
      setCancelling((s) => {
        const n = new Set(s);
        n.delete(id);
        return n;
      });
    }
  };

  const chips: Chip[] = [
    ...(inbox?.placed ?? []).map((p): Chip => ({ key: `placed:${p.submissionId}`, mode: 'steer', state: 'consumed' })),
    ...(inbox?.queue ?? []).map(
      (q): Chip => ({
        key: `queue:${q.submissionId}`,
        mode: q.mode,
        state: withdrawn.has(q.submissionId) ? 'withdrawn' : 'queued',
        text: q.preview,
        submissionId: q.submissionId,
      })
    ),
    ...sending.map(
      (s): Chip => ({
        key: `sending:${s.requestId}`,
        mode: s.mode,
        state: s.rejected ? 'rejected' : 'sending',
        text: s.text,
      })
    ),
  ];
  if (chips.length === 0) return null;
  return (
    <ul aria-label="Queued messages" className="mb-2 flex flex-wrap gap-1.5 px-1" data-testid="queued-chips">
      {chips.map((c) => (
        <li
          key={c.key}
          data-state={c.state}
          data-mode={c.mode}
          className={`flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${
            c.state === 'rejected'
              ? 'border-red-200 bg-red-50 text-red-700'
              : c.state === 'withdrawn'
                ? 'border-neutral-200 bg-neutral-50 text-neutral-400 line-through'
                : 'border-neutral-200 bg-neutral-50 text-neutral-700'
          }`}
        >
          <span className="font-medium">{MODE_LABEL[c.mode]}</span>
          {c.text && <span className="truncate max-w-[200px] text-neutral-500">{c.text}</span>}
          <span className="text-neutral-400">{c.state}</span>
          {onCancel && c.state === 'queued' && c.submissionId && (
            <button
              type="button"
              aria-label={`Cancel queued message${c.text ? `: ${c.text}` : ''}`}
              disabled={cancelling.has(c.submissionId)}
              onClick={() => void cancel(c.submissionId as string)}
              className="-mr-1 rounded-full px-1 text-neutral-400 hover:text-neutral-700 disabled:opacity-50"
            >
              ×
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}
