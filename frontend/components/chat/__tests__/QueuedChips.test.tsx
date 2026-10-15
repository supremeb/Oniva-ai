import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { InboxState } from '@/lib/engine';
import { QueuedChips } from '../QueuedChips';

const inbox: InboxState = {
  queue: [
    { submissionId: 'subm_1', mode: 'followUp', preview: 'then summarise the doc', requestId: 'r1' },
    { submissionId: 'subm_2', mode: 'steer' },
  ],
  placed: [{ submissionId: 'subm_0', runId: 'run_1' }],
};

describe('QueuedChips', () => {
  it('shows preview text when present and tolerates older engines without it', () => {
    render(<QueuedChips inbox={inbox} onCancel={jest.fn()} />);
    expect(screen.getByText('then summarise the doc')).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(3);
  });

  it('cancels a queued chip and shows it withdrawn until the inbox drops it', async () => {
    const onCancel = jest.fn().mockResolvedValue(undefined);
    render(<QueuedChips inbox={inbox} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole('button', { name: /cancel queued message: then summarise/i }));
    expect(onCancel).toHaveBeenCalledWith('subm_1');
    await waitFor(() =>
      expect(screen.getByText('then summarise the doc').closest('li')).toHaveAttribute('data-state', 'withdrawn')
    );
    expect(screen.getAllByRole('button')).toHaveLength(1);
  });

  it('keeps the chip queued when cancel is refused (already started)', async () => {
    const onCancel = jest.fn().mockRejectedValue(new Error('409'));
    render(<QueuedChips inbox={inbox} onCancel={onCancel} />);
    fireEvent.click(screen.getAllByRole('button')[0]);
    await waitFor(() => expect(screen.getAllByRole('button')[0]).toBeEnabled());
    expect(screen.getByText('then summarise the doc').closest('li')).toHaveAttribute('data-state', 'queued');
  });

  it('has no cancel button without a handler, and none on placed or sending chips', () => {
    render(
      <QueuedChips
        inbox={{ queue: [], placed: inbox.placed }}
        sending={[{ requestId: 'q', mode: 'steer', text: 'hi' }]}
        onCancel={jest.fn()}
      />
    );
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('renders a rejected send', () => {
    render(<QueuedChips inbox={null} sending={[{ requestId: 'q', mode: 'followUp', text: 'nope', rejected: true }]} />);
    expect(screen.getByText('nope').closest('li')).toHaveAttribute('data-state', 'rejected');
  });
});
