import React from 'react';
import { TextDecoder } from 'util';
import { act, renderHook } from '@testing-library/react';
import fixture from '../__fixtures__/frames.json';
import { EngineConversationClient, WatchHttpError } from '../client';
import { useEngineConversation } from '../useEngineConversation';
import { makeBody, sseFrame } from '../__fixtures__/stream';

Object.assign(globalThis, { TextDecoder });

const mockApiFetch = jest.fn();
jest.mock('@/lib/api', () => {
  class APIError extends Error {
    constructor(message: string, public status?: number, public data?: unknown) {
      super(message);
    }
  }
  return {
    API_BASE_URL: 'http://api.test',
    APIError,
    apiFetch: (...a: unknown[]) => mockApiFetch(...a),
    attemptTokenRefresh: jest.fn(async () => true),
  };
});

type Body = ReturnType<typeof makeBody>;

/** A scripted watch endpoint: each call yields the next controllable stream. */
function fakeClient() {
  const calls: (number | null)[] = [];
  const bodies: Body[] = [];
  let failNext: Error | null = null;
  const client = {
    watch: jest.fn(async (_id: string, cursor: number | null, signal: AbortSignal) => {
      calls.push(cursor);
      if (failNext) {
        const e = failNext;
        failNext = null;
        throw e;
      }
      const b = makeBody(signal);
      bodies.push(b);
      return { body: b.body } as unknown as Response;
    }),
    submit: jest.fn(async () => ({})),
    abort: jest.fn(),
    cancel: jest.fn(async () => ({})),
    decide: jest.fn(),
  };
  return { client: client as unknown as EngineConversationClient, raw: client, calls, bodies, failWith: (e: Error) => (failNext = e) };
}

const init = fixture.initial;
const [f1, f2, f3, f4, f5] = fixture.frames;
const settle = async () => {
  await act(async () => {
    await jest.advanceTimersByTimeAsync(0);
  });
};
const send = async (b: Body, frame: { cursor: number; [k: string]: unknown }, kind: 'patch' | 'snapshot' = 'patch') => {
  b.push(sseFrame(frame, kind));
  await settle();
};
const opts = (c: EngineConversationClient) => ({ client: c, watcher: { random: () => 0.5 } });

beforeEach(() => jest.useFakeTimers());
afterEach(() => jest.useRealTimers());

describe('useEngineConversation', () => {
  it('AE4: resumes with Last-Event-ID = last cursor and ends at the server final view', async () => {
    const f = fakeClient();
    const { result } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    expect(result.current.connection).toBe('connecting');
    await send(f.bodies[0], { cursor: 0, snapshot: init } as never, 'snapshot');
    expect(result.current.connection).toBe('live');
    await send(f.bodies[0], f1);
    await send(f.bodies[0], f2);

    f.bodies[0].end(); // stream cut
    await settle();
    expect(result.current.connection).toBe('reconnecting');
    await act(async () => {
      await jest.advanceTimersByTimeAsync(1000);
    });
    expect(f.calls).toEqual([null, f2.cursor]);
    expect(result.current.connection).toBe('reconnecting'); // until first frame
    await send(f.bodies[1], f3);
    await send(f.bodies[1], f4);
    await send(f.bodies[1], f5);
    expect(result.current.connection).toBe('live');
    expect(result.current.view).toEqual({ ...fixture.final, questions: { items: [] } }); // parser default for the pre-questions fixture
    expect(result.current.cursor).toBe(f5.cursor);
  });

  it('AE3: a snapshot with a non-empty live renders without any submit', async () => {
    const f = fakeClient();
    const { result } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    const withLive = { ...init, live: { runId: 'r1', taskId: 'k', message: { role: 'assistant' } } };
    await send(f.bodies[0], { cursor: 7, snapshot: withLive } as never, 'snapshot');
    expect(result.current.view?.live.runId).toBe('r1');
    expect(f.raw.submit).not.toHaveBeenCalled();
  });

  it('ignores a patch whose cursor is not greater than the current one', async () => {
    const f = fakeClient();
    const { result } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    await send(f.bodies[0], { cursor: 0, snapshot: init } as never, 'snapshot');
    await send(f.bodies[0], f1);
    const after = result.current.view;
    await send(f.bodies[0], f1); // replay: would fail to apply if it ran (index 0 exists)
    expect(result.current.view).toBe(after);
    expect(f.calls).toEqual([null]);
  });

  it('a patch that fails to apply resyncs: reconnect without a cursor, snapshot replaces state', async () => {
    const f = fakeClient();
    const { result } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    await send(f.bodies[0], { cursor: 0, snapshot: init } as never, 'snapshot');
    await send(f.bodies[0], { cursor: 99, patch: [{ op: 'replace', path: '/nope/x', value: 1 }] });
    expect(result.current.connection).toBe('reconnecting');
    await act(async () => {
      await jest.advanceTimersByTimeAsync(0);
    });
    expect(f.calls).toEqual([null, null]);
    await send(f.bodies[1], { cursor: 120, snapshot: fixture.final } as never, 'snapshot');
    expect(result.current.view).toEqual({ ...fixture.final, questions: { items: [] } }); // parser default for the pre-questions fixture
    expect(result.current.cursor).toBe(120);
  });

  it('drops the optimistic entry when an entry with its requestId arrives (AE2)', async () => {
    const f = fakeClient();
    const { result } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    await send(f.bodies[0], { cursor: 0, snapshot: init } as never, 'snapshot');
    let id = '';
    await act(async () => {
      id = await result.current.submit({ text: 'hello' });
    });
    expect(result.current.pending).toEqual([{ requestId: id, text: 'hello', status: 'sending' }]);
    expect(f.raw.submit).toHaveBeenCalledWith('c1', { requestId: id, text: 'hello' });
    const entry = { ...fixture.final.entries[0], payload: { content: 'hello', requestId: id } };
    await send(f.bodies[0], { cursor: 2, patch: [{ op: 'add', path: '/entries/0', value: entry }] });
    expect(result.current.pending).toEqual([]);
  });

  it('a snapshot drops stale optimistic entries that now exist', async () => {
    const f = fakeClient();
    const { result } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    await send(f.bodies[0], { cursor: 0, snapshot: init } as never, 'snapshot');
    let id = '';
    await act(async () => {
      id = await result.current.submit({ text: 'hi' });
    });
    const entry = { ...fixture.final.entries[0], payload: { requestId: id } };
    await send(f.bodies[0], { cursor: 5, snapshot: { ...init, entries: [entry] } } as never, 'snapshot');
    expect(result.current.pending).toEqual([]);
  });

  it('marks a failed submit and keeps the entry for retry with the same id', async () => {
    const f = fakeClient();
    f.raw.submit.mockRejectedValueOnce(new Error('boom'));
    const { result } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    await act(async () => {
      await result.current.submit({ text: 'x' }).catch(() => {});
    });
    expect(result.current.pending[0].status).toBe('failed');
  });

  it('cancelSubmission calls client.cancel for the conversation', async () => {
    const f = fakeClient();
    const { result } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    await act(async () => {
      await result.current.cancelSubmission('subm_1');
    });
    expect(f.raw.cancel).toHaveBeenCalledWith('c1', 'subm_1');
  });

  it('strict-mode double mount opens exactly one connection', async () => {
    const f = fakeClient();
    const wrapper = ({ children }: { children: React.ReactNode }) => <React.StrictMode>{children}</React.StrictMode>;
    renderHook(() => useEngineConversation('c1', opts(f.client)), { wrapper });
    await settle();
    expect(f.raw.watch).toHaveBeenCalledTimes(1);
  });

  it('closes the stream on unmount', async () => {
    const f = fakeClient();
    const { result, unmount } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    unmount();
    await settle();
    expect(result.current.connection).not.toBe('closed'); // last rendered value; no throw
    await act(async () => {
      await jest.advanceTimersByTimeAsync(60_000);
    });
    expect(f.raw.watch).toHaveBeenCalledTimes(1);
  });

  it('banner: reconnecting after a drop, live on first frame, stalled after 45 s of silence in a run', async () => {
    const f = fakeClient();
    const { result } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    const running = { ...init, live: { runId: 'r1', taskId: 'k', message: null } };
    await send(f.bodies[0], { cursor: 1, snapshot: running } as never, 'snapshot');
    expect(result.current.connection).toBe('live');
    // Heartbeat comments keep it live.
    await act(async () => {
      await jest.advanceTimersByTimeAsync(40_000);
    });
    f.bodies[0].push(': ping\n\n');
    await settle();
    await act(async () => {
      await jest.advanceTimersByTimeAsync(40_000);
    });
    expect(result.current.connection).toBe('live');
    await act(async () => {
      await jest.advanceTimersByTimeAsync(6_000);
    });
    expect(result.current.connection).toBe('stalled');
    await send(f.bodies[0], { cursor: 3, patch: [] });
    expect(result.current.connection).toBe('live');
    f.bodies[0].fail();
    await settle();
    expect(result.current.connection).toBe('reconnecting');
  });

  it('does not report stalled when idle', async () => {
    const f = fakeClient();
    const { result } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    await send(f.bodies[0], { cursor: 1, snapshot: init } as never, 'snapshot');
    await act(async () => {
      await jest.advanceTimersByTimeAsync(120_000);
    });
    expect(result.current.connection).toBe('live');
  });

  it('backs off exponentially with a cap and honors Retry-After on 503', async () => {
    const f = fakeClient();
    f.failWith(new WatchHttpError(503, 8000));
    const { result } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    expect(result.current.connection).toBe('reconnecting');
    await act(async () => {
      await jest.advanceTimersByTimeAsync(7_900);
    });
    expect(f.raw.watch).toHaveBeenCalledTimes(1);
    await act(async () => {
      await jest.advanceTimersByTimeAsync(200);
    });
    expect(f.raw.watch).toHaveBeenCalledTimes(2);
  });

  it('stops on 404 with a terminal error', async () => {
    const f = fakeClient();
    f.failWith(new WatchHttpError(404, null));
    const { result } = renderHook(() => useEngineConversation('c1', opts(f.client)));
    await settle();
    expect(result.current.connection).toBe('closed');
    expect(result.current.error).toBe('not_found');
    await act(async () => {
      await jest.advanceTimersByTimeAsync(60_000);
    });
    expect(f.raw.watch).toHaveBeenCalledTimes(1);
  });
});

describe('EngineConversationClient.submit', () => {
  beforeEach(() => {
    jest.useRealTimers();
    mockApiFetch.mockReset();
  });

  it('AE2: retries a timeout and a 503 with the same requestId and Idempotency-Key', async () => {
    const { APIError } = jest.requireMock('@/lib/api');
    mockApiFetch
      .mockRejectedValueOnce(new APIError('timeout'))
      .mockRejectedValueOnce(new APIError('busy', 503))
      .mockResolvedValueOnce({ ok: true });
    const sleep = jest.fn(async () => {});
    const c = new EngineConversationClient({ sleep });
    await expect(c.submit('c1', { requestId: 'r-1', text: 'hi', whenBusy: 'steer' })).resolves.toEqual({ ok: true });
    expect(mockApiFetch).toHaveBeenCalledTimes(3);
    for (const [url, init] of mockApiFetch.mock.calls) {
      expect(url).toBe('/api/v1/engine/conversations/c1/submissions');
      expect(init.headers['Idempotency-Key']).toBe('r-1');
      expect(JSON.parse(init.body)).toEqual({ requestId: 'r-1', text: 'hi', whenBusy: 'steer' });
    }
    expect(sleep).toHaveBeenCalledTimes(2);
  });

  it('does not retry a 4xx and gives up after the backoff list', async () => {
    const { APIError } = jest.requireMock('@/lib/api');
    mockApiFetch.mockRejectedValueOnce(new APIError('bad', 422));
    const c = new EngineConversationClient({ sleep: async () => {} });
    await expect(c.submit('c1', { requestId: 'a' })).rejects.toThrow('bad');
    expect(mockApiFetch).toHaveBeenCalledTimes(1);
    mockApiFetch.mockReset().mockRejectedValue(new APIError('busy', 503));
    await expect(c.submit('c1', { requestId: 'a' })).rejects.toThrow('busy');
    expect(mockApiFetch).toHaveBeenCalledTimes(4);
  });
});
