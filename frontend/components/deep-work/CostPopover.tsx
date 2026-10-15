'use client';

import { useId, useState } from 'react';
import type { ConversationCost } from '@/lib/engine/cost';
import { formatUsd } from '@/components/chat/CostBadge';

const PURPOSE_LABEL: Record<string, string> = {
  'deep_work.orchestrator': 'Orchestrator',
  'deep_work.worker': 'Workers',
  'deep_work.reflection': 'Reflection',
  'deep_work.synthesis': 'Synthesis',
  'deep_work.output': 'Output',
  generation: 'Generation',
  compaction: 'Compaction',
  orchestrator: 'Orchestrator',
  worker: 'Workers',
  reflection: 'Reflection',
  output: 'Output',
};

const label = (purpose: string) =>
  PURPOSE_LABEL[purpose] ??
  purpose
    .replace(/^deep_work\./, '')
    .replace(/[_.]/g, ' ')
    .replace(/^./, (c) => c.toUpperCase());

const tokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k` : String(n));

function price(usd: number, unpriced: boolean): string {
  if (!unpriced) return formatUsd(usd);
  return usd > 0 ? `${formatUsd(usd)}+` : 'n/a';
}

/**
 * Per-stage (or per-purpose) cost breakdown for the card header. Renders nothing without a
 * breakdown (older backends, or while the ledger has not answered yet).
 */
export function CostPopover({ cost }: { cost: ConversationCost | null }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  // Per-stage rows when the backend sends them; older backends fall back to per-purpose.
  const stageRows = cost?.byStage && cost.byStage.length > 0 ? cost.byStage : null;
  const rows = stageRows
    ? stageRows.map((r) => ({ key: r.stage, ...r }))
    : (cost?.byPurpose ?? []).map((r) => ({ key: r.purpose, ...r }));
  if (!cost || rows.length === 0) return null;
  return (
    <span className="relative inline-block">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label="Cost by phase"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Escape') setOpen(false);
        }}
        className="ml-1 rounded px-1 text-xs text-neutral-500 underline hover:text-neutral-800"
      >
        Details
      </button>
      {open && (
        <div
          id={panelId}
          role="region"
          aria-label="Cost by phase"
          data-testid="cost-popover"
          onClick={(e) => e.stopPropagation()}
          className="absolute right-0 z-20 mt-1 w-64 rounded-lg border border-neutral-200 bg-white p-3 text-xs shadow-lg"
        >
          <div className="mb-2 flex justify-between font-medium text-neutral-900">
            <span>Total</span>
            <span data-testid="cost-popover-total">{price(cost.totalUsd, cost.unpriced)}</span>
          </div>
          <ul className="space-y-1">
            {rows.map((r) => (
              <li key={r.key} className="flex justify-between gap-2 tabular-nums text-neutral-600" data-purpose={r.key}>
                <span>{label(r.key)}</span>
                <span className="text-neutral-400">
                  {tokens(r.inputTokens)} in / {tokens(r.outputTokens)} out
                </span>
                <span>{price(r.usd, r.unpriced)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </span>
  );
}
