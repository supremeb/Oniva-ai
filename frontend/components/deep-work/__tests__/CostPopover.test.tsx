import { fireEvent, render, screen } from '@testing-library/react';
import { CostPopover } from '../CostPopover';
import type { ConversationCost } from '@/lib/engine/cost';

const base: ConversationCost = { totalUsd: 0.02, inputTokens: 10, outputTokens: 5, unpriced: true, runs: [] };

describe('CostPopover', () => {
  it('renders nothing without a breakdown', () => {
    const { container } = render(<CostPopover cost={base} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('partially unpriced rows show a plus, fully unpriced show n/a, and Escape closes', () => {
    render(
      <CostPopover
        cost={{
          ...base,
          byPurpose: [
            { purpose: 'deep_work.synthesis', usd: 0.02, inputTokens: 12000, outputTokens: 800, unpriced: true },
            { purpose: 'custom_thing', usd: 0, inputTokens: 1, outputTokens: 1, unpriced: true },
          ],
        }}
      />,
    );
    const btn = screen.getByRole('button', { name: 'Cost by phase' });
    expect(btn).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(btn);
    expect(btn).toHaveAttribute('aria-expanded', 'true');
    const rows = screen.getByTestId('cost-popover').querySelectorAll('li');
    expect(rows[0]).toHaveTextContent('Synthesis');
    expect(rows[0]).toHaveTextContent('$0.020+');
    expect(rows[0]).toHaveTextContent('12k in');
    expect(rows[1]).toHaveTextContent('Custom thing');
    expect(rows[1]).toHaveTextContent('n/a');
    fireEvent.keyDown(btn, { key: 'Escape' });
    expect(screen.queryByTestId('cost-popover')).not.toBeInTheDocument();
  });

  it('shows per-stage rows when byStage is present, with n/a for unpriced and byPurpose ignored', () => {
    render(
      <CostPopover
        cost={{
          ...base,
          byPurpose: [{ purpose: 'generation', usd: 0.02, inputTokens: 1, outputTokens: 1, unpriced: false }],
          byStage: [
            { stage: 'orchestrator', usd: 0.01, inputTokens: 2000, outputTokens: 100, unpriced: false },
            { stage: 'worker', usd: 0, inputTokens: 9000, outputTokens: 500, unpriced: true },
            { stage: 'reflection', usd: 0.004, inputTokens: 300, outputTokens: 50, unpriced: false },
            { stage: 'output', usd: 0.006, inputTokens: 400, outputTokens: 60, unpriced: false },
            { stage: 'compaction', usd: 0.001, inputTokens: 10, outputTokens: 2, unpriced: false },
          ],
        }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Cost by phase' }));
    const rows = screen.getByTestId('cost-popover').querySelectorAll('li');
    expect(rows).toHaveLength(5);
    expect(rows[0]).toHaveTextContent('Orchestrator');
    expect(rows[0]).toHaveTextContent('$0.010');
    expect(rows[1]).toHaveTextContent('Workers');
    expect(rows[1]).toHaveTextContent('n/a');
    expect(rows[2]).toHaveTextContent('Reflection');
    expect(rows[3]).toHaveTextContent('Output');
    expect(rows[4]).toHaveTextContent('Compaction');
    expect(screen.queryByText('Generation')).not.toBeInTheDocument();
  });

  it('falls back to byPurpose when byStage is empty', () => {
    render(
      <CostPopover
        cost={{
          ...base,
          byStage: [],
          byPurpose: [{ purpose: 'generation', usd: 0.02, inputTokens: 1, outputTokens: 1, unpriced: false }],
        }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Cost by phase' }));
    expect(screen.getByTestId('cost-popover').querySelectorAll('li')[0]).toHaveTextContent('Generation');
  });
});
