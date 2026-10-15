import React from 'react';
import { act, render, renderHook, screen, fireEvent } from '@testing-library/react';
import { CostBadge, runForTurn } from '../CostBadge';
import { useConversationCost, type ConversationCost } from '@/lib/engine/cost';
import type { ConversationView } from '@/lib/engine/types';

jest.mock('@/lib/api', () => ({ apiFetch: jest.fn() }));

const COST: ConversationCost = {
  totalUsd: 0.017, inputTokens: 180, outputTokens: 35, unpriced: false,
  runs: [
    { runId: 'r1', usd: 0.015, inputTokens: 150, outputTokens: 30, unpriced: false, startedAt: 'a' },
    { runId: 'r2', usd: 0, inputTokens: 30, outputTokens: 5, unpriced: true, startedAt: 'b' },
  ],
};

const view = (runId: string | null): ConversationView =>
  ({
    entries: [], live: { runId, taskId: null, message: null },
    usage: { requests: 1, total: { requests: 1, inputTokens: 1200, outputTokens: 300, cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: null }, byModel: {}, byPurpose: {} },
  }) as unknown as ConversationView;

describe('CostBadge', () => {
  it('shows per-turn values and n/a for an unpriced run; total equals the ledger', () => {
    const { rerender } = render(<CostBadge variant="turn" run={runForTurn(COST, 0)} />);
    expect(screen.getByTestId('cost-badge-turn').textContent).toBe('150 in / 30 out · $0.015');
    rerender(<CostBadge variant="turn" run={runForTurn(COST, 1)} />);
    expect(screen.getByTestId('cost-badge-turn').textContent).toContain('n/a');
    rerender(<CostBadge variant="total" cost={COST} />);
    expect(screen.getByTestId('cost-badge-total').textContent).toContain('180 in / 35 out');
  });

  it('shows tokens only, no dollars, while a run is active', () => {
    render(<CostBadge variant="turn" run={COST.runs[0]} active liveTokens={{ input: 1200, output: 300 }} />);
    const t = screen.getByTestId('cost-badge-turn').textContent!;
    expect(t).toContain('estimate');
    expect(t).not.toContain('$');
  });

  it('manual refresh calls back', () => {
    const onRefresh = jest.fn();
    render(<CostBadge variant="total" cost={COST} onRefresh={onRefresh} />);
    fireEvent.click(screen.getByLabelText('Refresh cost'));
    expect(onRefresh).toHaveBeenCalled();
  });
});

describe('useConversationCost', () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it('fetches on rejoin, then once at run end plus one delayed refetch; never polls', async () => {
    const fetcher = jest.fn().mockResolvedValue(COST);
    const { result, rerender } = renderHook(({ v }) => useConversationCost('c1', v, fetcher, 2000), {
      initialProps: { v: view('r1') as ConversationView | null },
    });
    await act(async () => { rerender({ v: view('r1') }); });
    expect(fetcher).toHaveBeenCalledTimes(1); // rejoin fetch once the view exists
    expect(result.current.active).toBe(true);
    expect(result.current.liveTokens).toEqual({ input: 1200, output: 300 });

    await act(async () => { jest.advanceTimersByTime(30_000); });
    expect(fetcher).toHaveBeenCalledTimes(1); // no polling while active

    await act(async () => { rerender({ v: view(null) }); });
    expect(fetcher).toHaveBeenCalledTimes(2); // terminal: immediate fetch
    await act(async () => { jest.advanceTimersByTime(2000); });
    expect(fetcher).toHaveBeenCalledTimes(3); // late usage report
    await act(async () => { jest.advanceTimersByTime(30_000); });
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(result.current.cost?.totalUsd).toBe(0.017);

    await act(async () => { await result.current.refresh(); });
    expect(fetcher).toHaveBeenCalledTimes(4);
  });
});
