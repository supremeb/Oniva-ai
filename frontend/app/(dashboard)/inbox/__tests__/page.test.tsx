import { render, screen, fireEvent } from '@testing-library/react';
import InboxPage from '../page';
import { useInbox } from '@/hooks/useInbox';

jest.mock('@/hooks/useInbox');
jest.mock('@/lib/engine/client', () => ({ EngineConversationClient: jest.fn().mockImplementation(() => ({ decide: jest.fn() })) }));
const mocked = useInbox as jest.Mock;

test('renders items', () => {
  mocked.mockReturnValue({ items: [{ id: 'fleet:1', kind: 'fleet', title: 'Run failed', summary: '', severity: 'low', source: { type: 'b', id: '1' }, actions: [], href: '/fleet' }], loading: false, error: null, refetch: jest.fn() });
  render(<InboxPage />);
  expect(screen.getByRole('heading', { name: 'Needs attention' })).toBeInTheDocument();
  expect(screen.getByText('Run failed')).toBeInTheDocument();
});

test('empty state', () => {
  mocked.mockReturnValue({ items: [], loading: false, error: null, refetch: jest.fn() });
  render(<InboxPage />);
  expect(screen.getByText('Nothing needs your attention.')).toBeInTheDocument();
});

test('error state with retry', () => {
  const refetch = jest.fn();
  mocked.mockReturnValue({ items: [], loading: false, error: 'Could not load your inbox. Try again.', refetch });
  render(<InboxPage />);
  expect(screen.getByRole('alert')).toHaveTextContent('Could not load your inbox');
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(refetch).toHaveBeenCalled();
});

test('loading state', () => {
  mocked.mockReturnValue({ items: [], loading: true, error: null, refetch: jest.fn() });
  render(<InboxPage />);
  expect(screen.getByText('Loading...')).toBeInTheDocument();
});
