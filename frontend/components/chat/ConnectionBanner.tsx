'use client';

import type { Connection } from '@/lib/engine';

export interface ConnectionBannerProps {
  connection: Connection;
  /** Terminal error from the watcher (`not_found` / `forbidden`). */
  error: string | null;
  /** Seconds of silence, shown in the stalled copy. */
  stalledAfterSeconds?: number;
}

/**
 * Non-blocking connection state for engine chat (R12, KTD15). Clears itself on
 * the first frame because the watcher flips back to `live`.
 */
export function ConnectionBanner({ connection, error, stalledAfterSeconds = 45 }: ConnectionBannerProps) {
  let text: string | null = null;
  let tone: 'warn' | 'error' = 'warn';
  if (connection === 'reconnecting') {
    text = 'Reconnecting... your run continues on the server';
  } else if (connection === 'stalled') {
    text = `The run has not reported progress for ${stalledAfterSeconds} s`;
  } else if (connection === 'closed' && error) {
    tone = 'error';
    text = error === 'forbidden' ? 'You do not have access to this conversation.' : 'This conversation is not available.';
  }
  if (!text) return null;
  return (
    <div
      role="status"
      data-testid="connection-banner"
      data-connection={connection}
      className={
        tone === 'error'
          ? 'mx-6 mb-2 rounded-lg border border-destructive/20 bg-destructive/10 px-3 py-2 text-sm text-destructive'
          : 'mx-6 mb-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 flex items-center gap-2'
      }
    >
      {tone === 'warn' && <span className="inline-block h-2 w-2 rounded-full bg-amber-500 animate-pulse" aria-hidden />}
      {text}
    </div>
  );
}
