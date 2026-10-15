'use client';

import { activeRunId } from './run';
import { useCallback, useEffect, useRef, useState } from 'react';
import { apiFetch } from '@/lib/api';
import type { ConversationView } from './types';

export interface RunCost {
  runId: string;
  usd: number;
  inputTokens: number;
  outputTokens: number;
  unpriced: boolean;
  startedAt: string | null;
}

export interface PurposeCost {
  /** generation | compaction (ledger purpose); rows without one count as generation. */
  purpose: string;
  usd: number;
  inputTokens: number;
  outputTokens: number;
  unpriced: boolean;
}

export interface ConversationCost {
  totalUsd: number;
  inputTokens: number;
  outputTokens: number;
  unpriced: boolean;
  runs: RunCost[];
  /** Same rows as `runs`, partitioned by purpose (root plus subagent usage). Absent on older backends. */
  byPurpose?: PurposeCost[];
}

export function fetchConversationCost(conversationId: string): Promise<ConversationCost> {
  return apiFetch<ConversationCost>(`/api/v1/engine/conversations/${encodeURIComponent(conversationId)}/cost`, {
    timeout: 15000,
  });
}

/** Delay for the single follow-up fetch that picks up a usage report written after the terminal entry. */
export const LATE_USAGE_REFETCH_MS = 2000;

export interface UseConversationCost {
  cost: ConversationCost | null;
  /** True while a run is active: dollars are not shown, tokens come from `view.usage`. */
  active: boolean;
  /** Estimated tokens of the whole conversation so far, from the live view. */
  liveTokens: { input: number; output: number } | null;
  refresh(): Promise<void>;
}

/**
 * Cost from the ledger. No polling: fetch on mount (rejoin), once when a run ends
 * (plus one delayed refetch for late usage reports), and on manual refresh.
 */
export function useConversationCost(
  conversationId: string | null,
  view: ConversationView | null,
  fetcher: (id: string) => Promise<ConversationCost> = fetchConversationCost,
  lateMs: number = LATE_USAGE_REFETCH_MS
): UseConversationCost {
  const [cost, setCost] = useState<ConversationCost | null>(null);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const idRef = useRef(conversationId);
  idRef.current = conversationId;

  const refresh = useCallback(async () => {
    const id = idRef.current;
    if (!id) return;
    try {
      const next = await fetcherRef.current(id);
      if (idRef.current === id) setCost(next);
    } catch {
      /* keep the last known cost; the badge shows what it has */
    }
  }, []);

  const hasView = view !== null;
  const runId = activeRunId(view);
  const active = runId !== null;

  // Rejoin / conversation switch: one fetch once the view is first available.
  useEffect(() => {
    setCost(null);
    if (conversationId && hasView) void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, hasView]);

  // Run ended (active -> idle): one fetch now, one delayed for late usage reports.
  const prevRunId = useRef<string | null>(null);
  useEffect(() => {
    const ended = prevRunId.current !== null && runId === null;
    prevRunId.current = runId;
    if (!ended) return;
    void refresh();
    const t = setTimeout(() => void refresh(), lateMs);
    return () => clearTimeout(t);
  }, [runId, refresh, lateMs]);

  const total = view?.usage?.total;
  return {
    cost,
    active,
    liveTokens: total ? { input: total.inputTokens, output: total.outputTokens } : null,
    refresh,
  };
}
