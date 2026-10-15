import { TextDecoder } from 'util';
import { ConversationWatcher } from '../watcher';
import type { EngineConversationClient } from '../client';
import { makeBody } from '../__fixtures__/stream';

Object.assign(globalThis, { TextDecoder });

describe('ConversationWatcher restart', () => {
  it('mount/unmount/mount (strict mode) still connects, with a single stream', async () => {
    const watch = jest.fn(async (_id: string, _c: number | null, signal: AbortSignal) => ({ body: makeBody(signal).body }) as unknown as Response);
    const w = new ConversationWatcher('c1', { watch } as unknown as EngineConversationClient);
    w.start();
    w.close();
    w.start();
    await new Promise((r) => setTimeout(r, 20));
    expect(watch).toHaveBeenCalledTimes(1);
    expect(w.getState().connection).toBe('connecting');
    w.close();
  });

  it('can restart after the loop already ran', async () => {
    const watch = jest.fn(async (_id: string, _c: number | null, signal: AbortSignal) => ({ body: makeBody(signal).body }) as unknown as Response);
    const w = new ConversationWatcher('c1', { watch } as unknown as EngineConversationClient);
    w.start();
    await new Promise((r) => setTimeout(r, 20));
    w.close();
    expect(w.getState().connection).toBe('closed');
    w.start();
    await new Promise((r) => setTimeout(r, 20));
    expect(watch).toHaveBeenCalledTimes(2);
    expect(w.getState().connection).toBe('connecting');
    w.close();
  });
});
