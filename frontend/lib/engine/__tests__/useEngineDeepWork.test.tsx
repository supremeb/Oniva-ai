import { TextDecoder } from 'util';
import { act, renderHook } from '@testing-library/react';
import type { EngineConversationClient } from '../client';
import { useEngineDeepWork, useWorkerTranscript } from '../useEngineDeepWork';
import { makeBody, sseFrame } from '../__fixtures__/stream';
import type { ConversationView } from '../types';

Object.assign(globalThis, { TextDecoder });

jest.mock('@/lib/api', () => {
  class APIError extends Error {}
  return { API_BASE_URL: 'http://api.test', APIError, apiFetch: jest.fn(), attemptTokenRefresh: jest.fn(async () => true) };
});

const ZERO = { requests: 0, inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: null };
const baseView = (entries: unknown[] = [], subagents: unknown = {}): ConversationView =>
  ({
    entries,
    live: { runId: 'r', taskId: null, message: null },
    inbox: { queue: [], placed: [] },
    approvals: { items: [] },
    usage: { requests: 0, total: ZERO, byModel: {}, byPurpose: {} },
    agent: {},
    toolOutput: {},
    run: { active: true, runId: 'r' },
    subagents,
  }) as unknown as ConversationView;

const writeResult = (id: string) => ({
  id,
  conversationId: 'c',
  taskId: `t_${id}`,
  kind: 'tool_result',
  createdAt: 1,
  payload: { toolCallId: id, toolName: 'dw_write_section', status: 'ok', content: '{}', details: { workspace: ['report.md'] } },
});

/** A scripted watch source: each call yields the next controllable stream. */
function fakeSource() {
  const calls: { id: string; cursor: number | null }[] = [];
  const bodies: ReturnType<typeof makeBody>[] = [];
  const watch = jest.fn(async (id: string, cursor: number | null, signal: AbortSignal) => {
    calls.push({ id, cursor });
    const b = makeBody(signal);
    bodies.push(b);
    return { body: b.body } as unknown as Response;
  });
  return { watch, calls, bodies, client: { watch, submit: jest.fn(), abort: jest.fn(async () => ({})), cancel: jest.fn(), decide: jest.fn() } as unknown as EngineConversationClient };
}

const settle = async () => {
  await act(async () => {
    await jest.advanceTimersByTimeAsync(0);
  });
};
const snap = async (b: ReturnType<typeof makeBody>, cursor: number, view: ConversationView) => {
  b.push(sseFrame({ cursor, snapshot: view } as never, 'snapshot'));
  await settle();
};

beforeEach(() => jest.useFakeTimers());
afterEach(() => jest.useRealTimers());

describe('useEngineDeepWork', () => {
  it('derives deepWork from the single watch subscription and keeps it stable across identical frames', async () => {
    const src = fakeSource();
    const { result } = renderHook(() => useEngineDeepWork('conv_root', { client: src.client }));
    await settle();
    expect(src.calls).toEqual([{ id: 'conv_root', cursor: null }]);
    expect(result.current.deepWork.workers).toEqual([]);
    await snap(src.bodies[0], 1, baseView());
    expect(result.current.connection).toBe('live');
    expect(result.current.deepWork.status).toBe('executing');
    const first = result.current.deepWork;
    await snap(src.bodies[0], 2, baseView());
    expect(result.current.deepWork).toBe(first);
    expect(src.calls).toHaveLength(1);
  });

  it('refetches the workspace once, debounced, after write-tool results; not on unrelated frames', async () => {
    const src = fakeSource();
    const listWorkspace = jest.fn(async () => [{ name: 'report.md', path: 'report.md', size: 3, type: 'file', modified_at: 1 }]);
    const { result } = renderHook(() =>
      useEngineDeepWork('conv_root', { client: src.client, sessionId: 'sess1', api: { listWorkspace }, workspaceDebounceMs: 300 }),
    );
    await settle();
    await snap(src.bodies[0], 1, baseView());
    await act(async () => {
      await jest.advanceTimersByTimeAsync(300);
    });
    expect(listWorkspace).toHaveBeenCalledTimes(1); // initial load of a recovered session
    expect(listWorkspace).toHaveBeenLastCalledWith('sess1');

    await snap(src.bodies[0], 2, baseView([writeResult('a')]));
    await snap(src.bodies[0], 3, baseView([writeResult('a'), writeResult('b')]));
    expect(listWorkspace).toHaveBeenCalledTimes(1); // still inside the debounce
    await act(async () => {
      await jest.advanceTimersByTimeAsync(300);
    });
    expect(listWorkspace).toHaveBeenCalledTimes(2);
    expect(result.current.workspaceFiles.map((f) => f.name)).toEqual(['report.md']);

    await snap(src.bodies[0], 4, baseView([writeResult('a'), writeResult('b')]));
    await act(async () => {
      await jest.advanceTimersByTimeAsync(1000);
    });
    expect(listWorkspace).toHaveBeenCalledTimes(2);
  });

  it('steer posts once with a request id; stop cancels the session (abort without one)', async () => {
    const src = fakeSource();
    const steer = jest.fn(async () => ({}));
    const cancel = jest.fn(async () => ({}));
    const { result } = renderHook(() =>
      useEngineDeepWork('conv_root', { client: src.client, sessionId: 'sess1', api: { steer, cancel, listWorkspace: async () => [] } }),
    );
    await settle();
    let id = '';
    await act(async () => {
      id = await result.current.steer('focus on EU');
    });
    expect(steer).toHaveBeenCalledWith('sess1', 'focus on EU', id);
    await act(async () => {
      await result.current.stop();
    });
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(cancel).toHaveBeenCalledWith('sess1');

    const bare = renderHook(() => useEngineDeepWork('conv_root', { client: src.client }));
    await settle();
    await act(async () => {
      await bare.result.current.stop();
    });
    expect((src.client as unknown as { abort: jest.Mock }).abort).toHaveBeenCalledTimes(1);
  });
});

describe('useWorkerTranscript', () => {
  it('opens one connection per opened worker and closes it on collapse', async () => {
    const src = fakeSource();
    let child: string | null = null;
    const { result, rerender } = renderHook(() => useWorkerTranscript('sess1', child, { source: src }));
    await settle();
    expect(src.watch).not.toHaveBeenCalled(); // collapsed: no connection
    expect(result.current.view).toBeNull();

    child = 'child_w1';
    rerender();
    await settle();
    expect(src.calls).toEqual([{ id: 'child_w1', cursor: null }]);
    await snap(src.bodies[0], 1, baseView([{ id: 'e1', conversationId: 'child_w1', taskId: null, kind: 'user', createdAt: 1, payload: { content: 'go' } }]));
    expect(result.current.view?.entries).toHaveLength(1);
    expect(result.current.connection).toBe('live');

    const signal = src.watch.mock.calls[0]?.[2] as AbortSignal;
    child = null;
    rerender();
    await settle();
    expect(signal.aborted).toBe(true);
    expect(src.watch).toHaveBeenCalledTimes(1);

    child = 'child_w2';
    rerender();
    await settle();
    expect(src.calls.map((c) => c.id)).toEqual(['child_w1', 'child_w2']);
  });
});
