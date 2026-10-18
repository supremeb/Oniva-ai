import { render, screen, fireEvent } from '@testing-library/react';

const replace = jest.fn();
let search = '';
jest.mock('next/navigation', () => ({
  useRouter: () => ({ replace }),
  usePathname: () => '/fleet',
  useSearchParams: () => new URLSearchParams(search),
}));
jest.mock('@/components/fleet/FleetRunList', () => ({
  FleetRunList: ({ selectedId, onSelect }: any) => (
    <button data-testid="list" data-selected={selectedId ?? ''} onClick={() => onSelect({ id: 'job-1', agent_name: 'A', status: 'running', conversation_id: 'c1' })}>
      pick
    </button>
  ),
}));
jest.mock('@/components/fleet/FleetChat', () => ({
  FleetChat: ({ runId, conversationId }: any) => <div data-testid="chat">{runId}:{conversationId}</div>,
}));
const useFleetRun = jest.fn();
jest.mock('@/hooks/useFleetRuns', () => ({ useFleetRun: (...a: any[]) => useFleetRun(...a) }));

import FleetPage from '../page';

beforeEach(() => {
  replace.mockClear();
  useFleetRun.mockReset();
  useFleetRun.mockReturnValue({ data: undefined, isLoading: false });
  search = '';
});

it('writes ?run=<id> to the URL on selection', () => {
  render(<FleetPage />);
  fireEvent.click(screen.getByTestId('list'));
  expect(replace).toHaveBeenCalledWith('/fleet?run=job-1', { scroll: false });
});

it('restores the selected run from ?run= by looking the run up', () => {
  search = 'run=job-9';
  useFleetRun.mockReturnValue({ data: { id: 'job-9', agent_name: 'B', status: 'completed', conversation_id: 'conv-9' }, isLoading: false });
  render(<FleetPage />);
  expect(screen.getByTestId('chat')).toHaveTextContent('job-9:conv-9');
  expect(screen.getByTestId('list')).toHaveAttribute('data-selected', 'job-9');
});

it('shows the empty state with no ?run=', () => {
  render(<FleetPage />);
  expect(screen.queryByTestId('chat')).toBeNull();
});

it('shows not-found when the ?run= lookup finds nothing', () => {
  search = 'run=old-1';
  useFleetRun.mockReturnValue({ data: null, isLoading: false });
  render(<FleetPage />);
  expect(screen.getByText('Run not found')).toBeInTheDocument();
  expect(screen.queryByTestId('chat')).toBeNull();
});

it('a run picked while pending switches to the engine conversation once the live row has one', () => {
  search = 'run=job-1';
  useFleetRun.mockReturnValue({ data: undefined, isLoading: true });
  const { rerender } = render(<FleetPage />);
  fireEvent.click(screen.getByTestId('list')); // list mock reports conversation c1; pending case below
  useFleetRun.mockReturnValue({ data: { id: 'job-1', agent_name: 'A', status: 'running', conversation_id: 'conv-live' }, isLoading: false });
  rerender(<FleetPage />);
  expect(screen.getByTestId('chat')).toHaveTextContent('job-1:conv-live');
});
