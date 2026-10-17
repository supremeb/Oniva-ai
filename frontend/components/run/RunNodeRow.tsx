'use client';

import { memo, type KeyboardEvent, type ReactNode } from 'react';
import { CaretDown, CaretRight } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import { runStatusLabel } from '@/lib/run/status';
import type { RunNode } from '@/lib/run/tree';
import { RunStatusBadge } from './RunStatusBadge';

export function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function formatTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1).replace(/\.0$/, '')}k tokens` : `${n} tokens`;
}

function elapsedOf(node: RunNode, now: number): string | null {
  if (!node.startedAt) return null;
  const start = Date.parse(node.startedAt);
  if (!Number.isFinite(start)) return null;
  const end = node.endedAt ? Date.parse(node.endedAt) : now;
  return formatElapsed((Number.isFinite(end) ? end : now) - start);
}

function totalTokens(node: RunNode): number | null {
  const u = node.usage;
  if (!u) return null;
  if (typeof u.totalTokens === 'number') return u.totalTokens;
  if (typeof u.inputTokens === 'number' || typeof u.outputTokens === 'number') return (u.inputTokens ?? 0) + (u.outputTokens ?? 0);
  return null;
}

const NOTE: Partial<Record<RunNode['status'], string>> = {
  resuming: 'Picking up where it left off after a restart.',
  interrupted: 'Interrupted by a restart; the outcome is unknown.',
};

export interface RunNodeRowProps {
  node: RunNode;
  depth: number;
  now: number;
  expanded: boolean;
  tabbable: boolean;
  /** Number of children (before paging). */
  childCount: number;
  onToggle: (id: string) => void;
  onKeyDown: (e: KeyboardEvent<HTMLElement>, node: RunNode) => void;
  /** Rendered after the row inside the same treeitem (the children group). */
  children?: ReactNode;
}

export const RunNodeRow = memo(function RunNodeRow({ node, depth, now, expanded, tabbable, childCount, onToggle, onKeyDown, children }: RunNodeRowProps) {
  const elapsed = elapsedOf(node, now);
  const tokens = totalTokens(node);
  const hasKids = childCount > 0;
  return (
    <li
      role="treeitem"
      id={`run-node-${node.id}`}
      data-run-id={node.id}
      data-status={node.status}
      data-kind={node.kind}
      aria-level={depth + 1}
      aria-expanded={hasKids ? expanded : undefined}
      aria-label={`${node.label}, ${runStatusLabel(node.status)}`}
      tabIndex={tabbable ? 0 : -1}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        onKeyDown(e, node);
      }}
      className="list-none outline-none"
    >
      <div className={cn('flex items-center gap-2 rounded-md px-2 py-1 text-xs focus-visible:ring-2', node.status === 'failed' && 'bg-red-50/50')} style={{ paddingLeft: 8 + depth * 16 }}>
        {hasKids ? (
          <button
            type="button"
            tabIndex={-1}
            aria-label={`${expanded ? 'Collapse' : 'Expand'} ${node.label}`}
            onClick={() => onToggle(node.id)}
            className="flex-shrink-0 text-neutral-400 hover:text-neutral-700"
          >
            {expanded ? <CaretDown aria-hidden className="h-3 w-3" /> : <CaretRight aria-hidden className="h-3 w-3" />}
          </button>
        ) : (
          <span className="h-3 w-3 flex-shrink-0" aria-hidden />
        )}
        <RunStatusBadge status={node.status} />
        <span className="min-w-0 truncate font-medium text-neutral-800">{node.label}</span>
        <span className="ml-auto flex flex-shrink-0 items-center gap-2 tabular-nums text-neutral-400">
          {node.status === 'waiting' && node.waitUntil && (
            <time data-testid={`wake-${node.id}`} dateTime={node.waitUntil}>
              Wakes {new Date(node.waitUntil).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
            </time>
          )}
          {tokens !== null && <span>{formatTokens(tokens)}</span>}
          {elapsed && <span>{elapsed}</span>}
        </span>
      </div>
      {NOTE[node.status] && (
        <p className="text-[11px] text-neutral-500" style={{ paddingLeft: 8 + depth * 16 + 20 }}>
          {NOTE[node.status]}
        </p>
      )}
      {node.error && (
        <p className="line-clamp-3 text-[11px] text-red-600" style={{ paddingLeft: 8 + depth * 16 + 20 }}>
          {node.error}
        </p>
      )}
      {children}
    </li>
  );
});
