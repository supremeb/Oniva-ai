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
});
