'use client';

import type { ConversationCost, RunCost } from '@/lib/engine/cost';

const fmtTokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k` : String(n));

export function formatUsd(usd: number): string {
  if (usd === 0) return '$0.00';
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return usd < 1 ? `$${usd.toFixed(3)}` : `$${usd.toFixed(2)}`;
}

/** "cache 73%" for a ratio in 0-1; null when there is nothing to show. */
export function cacheLabel(ratio: number | null | undefined): string | null {
  return typeof ratio === 'number' && ratio > 0 ? `cache ${Math.round(ratio * 100)}%` : null;
}

/** The i-th run in start order is the i-th turn (the engine entry carries no runId on the wire). */
export function runForTurn(cost: ConversationCost | null, turnIndex: number): RunCost | null {
  return cost?.runs[turnIndex] ?? null;
}

interface CostBadgeProps {
  /** `turn` is the caption under a turn's last assistant message; `total` is the header figure. */
  variant: 'turn' | 'total';
  run?: RunCost | null;
  cost?: ConversationCost | null;
  /** A run is active: show estimated tokens only, no dollars. */
  active?: boolean;
  liveTokens?: { input: number; output: number } | null;
  onRefresh?: () => void;
}

export function CostBadge({ variant, run, cost, active, liveTokens, onRefresh }: CostBadgeProps) {
  const src = variant === 'turn' ? run ?? null : cost ?? null;
  let text: string;
  if (active) {
    text = liveTokens ? `~${fmtTokens(liveTokens.input + liveTokens.output)} tokens (estimate)` : '';
  } else if (!src) {
    return null;
  } else {
    const usd = 'totalUsd' in src ? src.totalUsd : src.usd;
    const price = src.unpriced ? (usd > 0 ? `${formatUsd(usd)}+ (some n/a)` : 'n/a') : formatUsd(usd);
    text = `${fmtTokens(src.inputTokens)} in / ${fmtTokens(src.outputTokens)} out · ${price}`;
    const cache = cacheLabel(src.cacheHitRatio);
    if (cache) text += ` · ${cache}`;
  }
  if (!text) return null;
  return (
    <span
      data-testid={`cost-badge-${variant}`}
      className={variant === 'turn' ? 'text-xs text-muted-foreground tabular-nums' : 'text-xs tabular-nums'}
      title={variant === 'total' ? 'Cost of this conversation' : 'Cost of this turn'}
    >
      {text}
      {variant === 'total' && onRefresh && !active ? (
        <button type="button" onClick={onRefresh} className="ml-2 underline" aria-label="Refresh cost">
          Refresh
        </button>
      ) : null}
    </span>
  );
}
