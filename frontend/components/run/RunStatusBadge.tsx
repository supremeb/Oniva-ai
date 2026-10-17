'use client';

import { memo } from 'react';
import { CheckCircle, CircleNotch, Clock, HourglassMedium, MinusCircle, Prohibit, Warning, WarningCircle, ArrowsClockwise } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import { runStatusLabel, type RunStatus } from '@/lib/run/status';

const STYLE: Record<RunStatus, string> = {
  queued: 'bg-neutral-100 text-neutral-600',
  running: 'bg-blue-50 text-blue-700',
  waiting: 'bg-amber-50 text-amber-700',
  resuming: 'bg-blue-50 text-blue-700',
  completed: 'bg-neutral-100 text-neutral-700',
  failed: 'bg-red-50 text-red-700',
  cancelled: 'bg-neutral-100 text-neutral-500',
  skipped: 'bg-neutral-50 text-neutral-400',
  interrupted: 'bg-amber-50 text-amber-800',
};

function StatusIcon({ status }: { status: RunStatus }) {
  const c = 'h-3 w-3 flex-shrink-0';
  switch (status) {
    case 'queued':
      return <Clock aria-hidden className={c} />;
    case 'running':
      return <CircleNotch aria-hidden className={cn(c, 'animate-spin motion-reduce:animate-none')} />;
    case 'waiting':
      return <HourglassMedium aria-hidden className={c} />;
    case 'resuming':
      return <ArrowsClockwise aria-hidden className={cn(c, 'animate-spin motion-reduce:animate-none')} />;
    case 'completed':
      return <CheckCircle aria-hidden className={c} weight="fill" />;
    case 'failed':
      return <WarningCircle aria-hidden className={c} weight="fill" />;
    case 'cancelled':
      return <Prohibit aria-hidden className={c} />;
    case 'skipped':
      return <MinusCircle aria-hidden className={c} />;
    case 'interrupted':
      return <Warning aria-hidden className={c} weight="fill" />;
  }
}

export const RunStatusBadge = memo(function RunStatusBadge({ status, className }: { status: RunStatus; className?: string }) {
  return (
    <span
      data-testid="run-status-badge"
      data-status={status}
      className={cn('inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[11px] font-medium', STYLE[status], className)}
    >
      <StatusIcon status={status} />
      {runStatusLabel(status)}
    </span>
  );
});
