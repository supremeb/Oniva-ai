import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { InboxList } from '../InboxList';
import type { InboxItem } from '@/lib/api/inbox';

const decide = jest.fn();
const answer = jest.fn();
jest.mock('@/lib/engine/client', () => ({
  EngineConversationClient: jest.fn().mockImplementation(() => ({
    decide: (...a: unknown[]) => decide(...a),
    answer: (...a: unknown[]) => answer(...a),
  })),
}));

const approval = (id: string, extra: Partial<InboxItem> = {}): InboxItem => ({
  id: `approval:${id}`, kind: 'approval', title: `Approve tool ${id}`, summary: 'needs ok', severity: 'medium',
  source: { type: 'tool_approval', id }, href: '/chat/c1',
  actions: [{ type: 'decide', endpoint: `/api/v1/engine/conversations/c-${id}/approvals/t-${id}/decision`, allowed: true }],
  ...extra,
});
const fleet: InboxItem = {
  id: 'fleet:1', kind: 'fleet', title: 'Run failed', summary: '', severity: 'low',
  source: { type: 'background_job', id: '1' }, actions: [], href: '/fleet',
};
const question: InboxItem = { ...fleet, id: 'question:1', kind: 'question', title: 'Which env?', href: '/chat/c9' };

beforeEach(() => decide.mockReset());

test('renders items in order with labels and links', () => {
  render(<InboxList items={[approval('a'), fleet, question]} />);
  const rows = screen.getAllByRole('listitem');
  expect(rows).toHaveLength(3);
  expect(rows[0]).toHaveTextContent('Approve tool a');
  expect(screen.getAllByRole('link', { name: 'Open' }).map((l) => l.getAttribute('href'))).toEqual(['/chat/c1', '/fleet']);
  expect(screen.getByRole('link', { name: 'Answer in chat' })).toHaveAttribute('href', '/chat/c9');
  expect(screen.getByRole('group', { name: 'Actions for Approve tool a' })).toBeInTheDocument();
});

test('approve calls decide with conversation and task ids and removes the row', async () => {
  decide.mockResolvedValue({});
  const onDecided = jest.fn();
  render(<InboxList items={[approval('a'), approval('b')]} onDecided={onDecided} />);
  fireEvent.click(screen.getAllByRole('button', { name: 'Approve' })[0]);
  await waitFor(() => expect(screen.queryByText('Approve tool a')).not.toBeInTheDocument());
  expect(decide).toHaveBeenCalledWith('c-a', 't-a', 'approve');
  expect(screen.getByText('Approve tool b')).toBeInTheDocument();
  expect(onDecided).toHaveBeenCalled();
});

test('deny sends deny', async () => {
  decide.mockResolvedValue({});
  render(<InboxList items={[approval('a')]} />);
  fireEvent.click(screen.getByRole('button', { name: 'Deny' }));
  await waitFor(() => expect(decide).toHaveBeenCalledWith('c-a', 't-a', 'deny'));
});

test('403 shows not-allowed copy on that row only', async () => {
  decide.mockRejectedValue({ status: 403, data: { detail: 'forbidden' } });
  render(<InboxList items={[approval('a'), approval('b')]} />);
  fireEvent.click(screen.getAllByRole('button', { name: 'Approve' })[0]);
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('You cannot decide this. Ask an admin.');
  expect(screen.getAllByRole('alert')).toHaveLength(1);
  expect(screen.getByText('Approve tool a')).toBeInTheDocument();
});

test('disabled action (self approval) disables buttons and explains', () => {
  const item = approval('a');
  item.actions = [{ ...item.actions[0], allowed: false, disabledReason: 'self_approval_forbidden' }];
  render(<InboxList items={[item]} />);
  expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled();
  expect(screen.getByText('Another admin must approve this.')).toBeInTheDocument();
});

test('filtering by kind narrows the list', () => {
  render(<InboxList items={[approval('a'), fleet, question]} />);
  fireEvent.click(screen.getByRole('button', { name: 'Fleet' }));
  expect(screen.getAllByRole('listitem')).toHaveLength(1);
  expect(screen.getByText('Run failed')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'All' }));
  expect(screen.getAllByRole('listitem')).toHaveLength(3);
});

test('empty state', () => {
  render(<InboxList items={[]} />);
  expect(screen.getByText('Nothing needs your attention.')).toBeInTheDocument();
});

describe('inline question answering (U12)', () => {
  const q = (extra: Partial<InboxItem> = {}): InboxItem => ({
    id: 'question:1', kind: 'question', title: 'Question', summary: 'Which env?', severity: 'medium',
    source: { type: 'tool_approval', id: '1' }, href: '/chat/c9',
    actions: [{ type: 'answer', endpoint: '/api/v1/engine/conversations/c-9/questions/t-9/answer', allowed: true }],
    ...extra,
  });
  beforeEach(() => answer.mockReset());
  const load = () => InboxList;

  test('answers inline with the typed text and the row disappears', async () => {
    answer.mockResolvedValue({});
    const List = load();
    const onDecided = jest.fn();
    render(<List items={[q(), fleet]} onDecided={onDecided} />);
    expect(screen.getByRole('link', { name: 'Answer in chat' })).toHaveAttribute('href', '/chat/c9');
    const send = screen.getByRole('button', { name: 'Send answer' });
    expect(send).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Answer for Question'), { target: { value: ' staging ' } });
    fireEvent.click(send);
    await waitFor(() => expect(screen.queryByText('Which env?')).not.toBeInTheDocument());
    expect(answer).toHaveBeenCalledWith('c-9', 't-9', { text: 'staging' });
    expect(answer).toHaveBeenCalledTimes(1);
    expect(onDecided).toHaveBeenCalled();
    expect(screen.getByText('Run failed')).toBeInTheDocument();
  });

  test('errors map like approvals and stay on that row; 5xx can be retried', async () => {
    const List = load();
    answer.mockRejectedValueOnce({ status: 403, data: { detail: 'not_authorized' } }).mockRejectedValueOnce({ status: 502 });
    render(<List items={[q()]} />);
    fireEvent.change(screen.getByLabelText('Answer for Question'), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send answer' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('You cannot decide this. Ask an admin.');
    fireEvent.click(screen.getByRole('button', { name: 'Send answer' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Could not send your decision. Try again.'));
    expect(screen.getByText('Which env?')).toBeInTheDocument();
  });

  test('a question without an allowed answer action only links to the chat', () => {
    const List = load();
    render(<List items={[q({ actions: [] })]} />);
    expect(screen.queryByRole('button', { name: 'Send answer' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Answer in chat' })).toBeInTheDocument();
  });
});
