'use client';

export interface CompactionMarkerProps {
  /** `running`: transient state while a compaction task runs. `done`: after the summary entry landed. */
  state: 'running' | 'done';
  className?: string;
}

/** Divider-style marker for context compaction (R9). */
export function CompactionMarker({ state, className = '' }: CompactionMarkerProps) {
  const running = state === 'running';
  return (
    <div
      role="status"
      data-testid="compaction-marker"
      data-state={state}
      className={`mt-6 flex items-center gap-3 text-xs text-neutral-500 ${className}`}
    >
      <span className="h-px flex-1 bg-neutral-200" aria-hidden />
      {running && <span className="inline-block h-2 w-2 rounded-full bg-neutral-400 animate-pulse" aria-hidden />}
      <span>{running ? 'Summarizing earlier messages' : 'Earlier messages summarized'}</span>
      <span className="h-px flex-1 bg-neutral-200" aria-hidden />
    </div>
  );
}
