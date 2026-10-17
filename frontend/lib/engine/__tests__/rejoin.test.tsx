import { TextDecoder } from 'util';
import { act, render, renderHook, screen } from '@testing-library/react';
import { EngineConversationClient } from '../client';
import { useEngineConversation } from '../useEngineConversation';
import { createViewAdapter } from '../viewToMessages';
import { makeBody, sseFrame } from '../__fixtures__/stream';
import { QueuedChips } from '@/components/chat/QueuedChips';
import { CompactionMarker } from '@/components/chat/CompactionMarker';
import { isCompactionRunning } from '../compaction';
import type { ConversationView, EntryRecord } from '../types';

Object.assign(globalThis, { TextDecoder });

jest.mock('@/lib/api', () => {
  class APIError extends Error {}
  return { API_BASE_URL: 'http://api.test', APIError, apiFetch: jest.fn(), attemptTokenRefresh: jest.fn(async () => true) };
});

type Body = ReturnType<typeof makeBody>;

function fakeClient() {
  const calls: (number | null)[] = [];
  const bodies: Body[] = [];
  const raw = {
    watch: jest.fn(async (_id: string, cursor: number | null, signal: AbortSignal) => {
      calls.push(cursor);
      const b = makeBody(signal);
      bodies.push(b);
      return { body: b.body } as unknown as Response;
    }),
    submit: jest.fn(async () => ({})),
    abort: jest.fn(),
    decide: jest.fn(),
  };
  return { client: raw as unknown as EngineConversationClient, raw, calls, bodies };
}

const settle = async () => {
  await act(async () => {
    await jest.advanceTimersByTimeAsync(0);
  });
};
type Frame = { cursor: number; [k: string]: unknown };
const send = async (b: Body, frame: Frame, kind: 'patch' | 'snapshot' = 'patch') => {
  b.push(sseFrame(frame, kind));
  await settle();
};
const opts = (c: EngineConversationClient) => ({ client: c, watcher: { random: () => 0.5 } });

const emptyUsage = {
  requests: 0,
  total: { requests: 0, inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: null },
  byModel: {},
  byPurpose: {},
};
const mkView = (over: Partial<ConversationView> = {}): ConversationView => ({
  entries: [],
  live: { runId: null, taskId: null, message: null },
  inbox: { queue: [], placed: [] },
  approvals: { items: [] },
  questions: { items: [] },
  usage: emptyUsage,
  agent: { systemPrompt: 'hi' },
  toolOutput: {},
  ...over,
});
const userEntry = (id: string, text: string, requestId?: string): EntryRecord => ({
  id,
  conversationId: 'c1',
  taskId: null,
  kind: 'user',
  payload: { submissionId: `s-${id}`, content: text, ...(requestId ? { requestId } : {}) },
  createdAt: 1,
});
const asstEntry = (id: string, text: string): EntryRecord => ({
  id,
  conversationId: 'c1',
  taskId: 'k1',
  kind: 'assistant',
  payload: { runId: 'r1', round: 0, message: { role: 'assistant', blocks: [{ type: 'text', text }] } },
  createdAt: 2,
});
const liveMsg = (text: string) => ({
  runId: 'r1',
  taskId: 'k1',
  message: { role: 'assistant', blocks: [{ type: 'text', text }] },
});
/** Occurrences of `needle` in the flat message text (richParts repeat it, so they are not counted). */
const count = (msgs: unknown, needle: string) =>
  (msgs as { content: string }[]).map((m) => m.content).join('\n').split(needle).length - 1;

beforeEach(() => jest.useFakeTimers());
afterEach(() => jest.useRealTimers());

describe('rejoin hardening', () => {
  it('snapshot while optimistic entries exist: no duplicate user text, pending cleared', async () => {
    const f = fakeClient();
    const { result } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    await send(f.bodies[0], { cursor: 0, snapshot: mkView() }, 'snapshot');
    let id = '';
    await act(async () => {
      id = await result.current.submit({ text: 'unique-hello' });
    });
    await act(async () => {
      await result.current.submit({ text: 'still-sending' });
    });
    expect(result.current.pending).toHaveLength(2);
    const snap = mkView({ entries: [userEntry('e1', 'unique-hello', id)], live: liveMsg('Working on') });
    await send(f.bodies[0], { cursor: 9, snapshot: snap }, 'snapshot');
    expect(result.current.pending.map((p) => p.text)).toEqual(['still-sending']);
    const msgs = createViewAdapter()(result.current.view as ConversationView);
    expect(count(msgs, 'unique-hello')).toBe(1);
    expect(count(msgs, 'Working on')).toBe(1);
  });

  it('cursor outside the retained window: snapshot equals a fresh load; live text is not duplicated', async () => {
    const f = fakeClient();
    const { result } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    await send(f.bodies[0], { cursor: 0, snapshot: mkView({ entries: [userEntry('e1', 'q')] }) }, 'snapshot');
    await send(f.bodies[0], { cursor: 2, patch: [{ op: 'replace', path: '/live', value: liveMsg('Part-one ') }] });
    f.bodies[0].end();
    await settle();
    await act(async () => {
      await jest.advanceTimersByTimeAsync(1000);
    });
    expect(f.calls).toEqual([null, 2]);
    // Server: cursor 2 is now outside the 500-frame window -> snapshot with progress.
    const server = mkView({
      entries: [userEntry('e1', 'q'), asstEntry('e2', 'Part-one ')],
      live: liveMsg('Part-two'),
    });
    await send(f.bodies[1], { cursor: 900, snapshot: server }, 'snapshot');
    expect(result.current.cursor).toBe(900);
    expect(f.calls).toHaveLength(2); // no extra reconnect

    // A fresh load (new hook) receives the same snapshot.
    const f2 = fakeClient();
    const fresh = renderHook(() => useEngineConversation('c1', opts(f2.client)));
    await settle();
    await send(f2.bodies[0], { cursor: 900, snapshot: server }, 'snapshot');
    expect(result.current.view).toEqual(fresh.result.current.view);

    const adapt = createViewAdapter();
    let msgs = adapt(result.current.view as ConversationView);
    expect(count(msgs, 'Part-one')).toBe(1);
    expect(count(msgs, 'Part-two')).toBe(1);
    // The stream keeps going from the snapshot cursor.
    await send(f.bodies[1], { cursor: 902, patch: [{ op: 'replace', path: '/live/message/blocks/0/text', value: 'Part-two more' }] });
    msgs = adapt(result.current.view as ConversationView);
    expect(count(msgs, 'Part-two more')).toBe(1);
    expect(count(msgs, 'Part-two')).toBe(1); // the earlier text was replaced, not appended to
  });

  it('a burst of >100 patches while hidden resolves to one snapshot and correct state', async () => {
    const f = fakeClient();
    const { result } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    await send(f.bodies[0], { cursor: 0, snapshot: mkView({ live: liveMsg('a') }) }, 'snapshot');
    // Server side: consumer fell > maxLag behind, so the 150 patches collapse into one snapshot.
    const final = mkView({ live: liveMsg('a'.repeat(151)) });
    await send(f.bodies[0], { cursor: 150, snapshot: final }, 'snapshot');
    expect(result.current.view).toEqual(final);
    expect(result.current.cursor).toBe(150);
    expect(f.calls).toEqual([null]);
    await send(f.bodies[0], { cursor: 151, patch: [{ op: 'replace', path: '/live/message/blocks/0/text', value: 'a'.repeat(152) }] });
    expect(count(createViewAdapter()(result.current.view as ConversationView), 'a'.repeat(152))).toBe(1);
  });

  it('150 patches delivered back-to-back in one chunk apply in order with the right cursor', async () => {
    const f = fakeClient();
    const { result } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    await send(f.bodies[0], { cursor: 0, snapshot: mkView({ live: liveMsg('x') }) }, 'snapshot');
    let chunk = '';
    for (let i = 1; i <= 150; i++) {
      chunk += sseFrame(
        { cursor: i, patch: [{ op: 'replace', path: '/live/message/blocks/0/text', value: `x${i}` }] } as Frame,
        'patch'
      );
    }
    f.bodies[0].push(chunk);
    await settle();
    expect(result.current.cursor).toBe(150);
    expect((result.current.view?.live.message as { blocks: { text: string }[] }).blocks[0].text).toBe('x150');
    expect(f.calls).toEqual([null]);
  });

  it('two hooks on one conversation converge; a steer from one shows as a chip in the other', async () => {
    const a = fakeClient();
    const b = fakeClient();
    const ha = renderHook(() => useEngineConversation('c1', opts(a.client)));
    const hb = renderHook(() => useEngineConversation('c1', opts(b.client)));
    await settle();
    const base = mkView({ entries: [userEntry('e1', 'go')], live: liveMsg('run') });
    const both = async (frame: Frame, kind: 'patch' | 'snapshot' = 'patch') => {
      await send(a.bodies[0], frame, kind);
      await send(b.bodies[0], frame, kind);
    };
    await both({ cursor: 3, snapshot: base }, 'snapshot');
    // Tab A steers (the optimistic entry is local to A); the server queues it for everyone.
    let id = '';
    await act(async () => {
      id = await ha.result.current.submit({ text: 'steer-text', whenBusy: 'steer' });
    });
    expect(hb.result.current.pending).toEqual([]);
    await both({
      cursor: 4,
      patch: [{ op: 'add', path: '/inbox/queue/0', value: { submissionId: 'sub-1', mode: 'steer' } }],
    });
    render(<QueuedChips inbox={hb.result.current.view?.inbox ?? null} />);
    const chips = screen.getAllByRole('listitem');
    expect(chips).toHaveLength(1);
    expect(chips[0].getAttribute('data-mode')).toBe('steer');
    expect(chips[0].getAttribute('data-state')).toBe('queued');
    const entry = userEntry('e2', 'steer-text', id);
    await both({
      cursor: 6,
      patch: [
        { op: 'remove', path: '/inbox/queue/0' },
        { op: 'add', path: '/inbox/placed/0', value: { submissionId: 'sub-1', runId: 'r1' } },
        { op: 'add', path: '/entries/1', value: entry },
      ],
    });
    expect(ha.result.current.pending).toEqual([]);
    expect(ha.result.current.view).toEqual(hb.result.current.view);
    // A late joiner (tab C via snapshot) converges as well.
    const c = fakeClient();
    const hc = renderHook(() => useEngineConversation('c1', opts(c.client)));
    await settle();
    await send(c.bodies[0], { cursor: 6, snapshot: ha.result.current.view as never }, 'snapshot');
    expect(hc.result.current.view).toEqual(ha.result.current.view);
    expect(count(createViewAdapter()(ha.result.current.view as ConversationView), 'steer-text')).toBe(1);
  });
});

describe('compaction marker', () => {
  it('renders the transient and done states', () => {
    const { rerender } = render(<CompactionMarker state="running" />);
    expect(screen.getByTestId('compaction-marker').textContent).toContain('Summarizing earlier messages');
    rerender(<CompactionMarker state="done" />);
    expect(screen.getByTestId('compaction-marker').getAttribute('data-state')).toBe('done');
  });

  it('a landed compaction entry becomes the adapter info marker; running is not visible in today\'s view', () => {
    const view = mkView({
      entries: [
        userEntry('e1', 'q'),
        { id: 'e2', conversationId: 'c1', taskId: 't', kind: 'compaction', payload: { summary: 'sum' }, createdAt: 3 },
      ],
    });
    expect(JSON.stringify(createViewAdapter()(view))).toContain('Earlier messages summarized');
    expect(isCompactionRunning(view)).toBe(false);
    expect(isCompactionRunning({ ...view, compaction: { running: 'task-1' } } as never)).toBe(true);
  });
});
