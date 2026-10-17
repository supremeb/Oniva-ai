import { renderHook, act, waitFor } from '@testing-library/react';
import { useInbox, useInboxCount } from '../useInbox';
import { listInbox, inboxCount } from '@/lib/api/inbox';

jest.mock('@/lib/api/inbox', () => ({ listInbox: jest.fn(), inboxCount: jest.fn() }));
const list = listInbox as jest.Mock;
const count = inboxCount as jest.Mock;
const item = (id: string) => ({ id, kind: 'fleet', title: id, summary: '', severity: 'low', source: { type: 't', id }, actions: [], href: '/fleet' });

beforeEach(() => { jest.useFakeTimers(); list.mockReset(); count.mockReset(); });
afterEach(() => jest.useRealTimers());

test('polls every 30s and a new item appears; unmount stops polling', async () => {
  list.mockResolvedValueOnce({ items: [item('a')] }).mockResolvedValue({ items: [item('a'), item('b')] });
  const { result, unmount } = renderHook(() => useInbox());
  await waitFor(() => expect(result.current.items).toHaveLength(1));
  await act(async () => { jest.advanceTimersByTime(30_000); });
  await waitFor(() => expect(result.current.items).toHaveLength(2));
  unmount();
  const calls = list.mock.calls.length;
  await act(async () => { jest.advanceTimersByTime(90_000); });
  expect(list.mock.calls.length).toBe(calls);
});

test('refetches on window focus', async () => {
  list.mockResolvedValue({ items: [] });
  renderHook(() => useInbox());
  await waitFor(() => expect(list).toHaveBeenCalledTimes(1));
  await act(async () => { window.dispatchEvent(new Event('focus')); });
  await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
});

test('does not poll while hidden', async () => {
  list.mockResolvedValue({ items: [] });
  renderHook(() => useInbox());
  await waitFor(() => expect(list).toHaveBeenCalledTimes(1));
  const spy = jest.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  await act(async () => { jest.advanceTimersByTime(30_000); });
  expect(list).toHaveBeenCalledTimes(1);
  spy.mockRestore();
});

test('error sets error message', async () => {
  list.mockRejectedValue(new Error('x'));
  const { result } = renderHook(() => useInbox());
  await waitFor(() => expect(result.current.error).toBeTruthy());
});

test('count hook returns total and keeps value on error', async () => {
  count.mockResolvedValueOnce({ total: 3, byKind: {} }).mockRejectedValue(new Error('x'));
  const { result } = renderHook(() => useInboxCount());
  await waitFor(() => expect(result.current).toBe(3));
  await act(async () => { jest.advanceTimersByTime(30_000); });
  expect(result.current).toBe(3);
});
