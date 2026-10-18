import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { FleetRunList } from '../FleetRunList';
import { useFleetRuns } from '@/hooks/useFleetRuns';
import type { FleetRunItem } from '@/lib/models';

jest.mock('@/hooks/useFleetRuns', () => ({ useFleetRuns: jest.fn() }));
const mockUse = useFleetRuns as jest.Mock;

const run = (id: string, status: string, over: Partial<FleetRunItem> = {}): FleetRunItem => ({
  id, agent_id: 'a', agent_name: `Agent ${id}`, agent_icon: null, job_type: 'background_agent', status, trigger: 'schedule',
  triggered_by: null, progress: 0, input_data: null, result: null, error: null, started_at: null, completed_at: null,
  created_at: new Date().toISOString(), duration_ms: null, total_tokens: null, ...over,
});

function setup(items: FleetRunItem[], onSelect = jest.fn()) {
  mockUse.mockReturnValue({ data: { items, total: items.length, page: 1, page_size: 50, has_more: false }, isLoading: false });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <FleetRunList selectedId={null} onSelect={onSelect} />
    </QueryClientProvider>,
  );
  return onSelect;
}

describe('FleetRunList', () => {
  it('badges every row with the shared status badge, mapping legacy values', () => {
    setup([run('1', 'pending'), run('2', 'error'), run('3', 'waiting'), run('4', 'resuming'), run('5', 'cancelled')]);
    const badges = screen.getAllByTestId('run-status-badge').map((b) => b.getAttribute('data-status'));
    expect(badges).toEqual(['queued', 'failed', 'waiting', 'resuming', 'cancelled']);
  });

  it('passes the engine conversation id and flag to onSelect', () => {
    const onSelect = setup([run('e1', 'running', { conversation_id: 'conv-9' }), run('l1', 'completed', { conversation_id: null })]);
    fireEvent.click(screen.getByText('Agent e1'));
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'e1', conversation_id: 'conv-9' }));
    fireEvent.click(screen.getByText('Agent l1'));
    expect(onSelect).toHaveBeenLastCalledWith(expect.objectContaining({ id: 'l1', conversation_id: null }));
  });

  it('shows the empty state', () => {
    setup([]);
    expect(screen.getByText('No agents running')).toBeInTheDocument();
  });
});
