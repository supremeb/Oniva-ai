import { TextEncoder } from 'util';

/** A controllable fake SSE body: only the reader surface readSse uses. */
export function makeBody(signal?: AbortSignal) {
  const queue: (Uint8Array | 'end' | Error)[] = [];
  let waiter: (() => void) | null = null;
  const enc = new TextEncoder();
  const wake = () => {
    waiter?.();
    waiter = null;
  };
  signal?.addEventListener('abort', () => {
    queue.push(Object.assign(new Error('aborted'), { name: 'AbortError' }));
    wake();
  });
  const body = {
    getReader() {
      return {
        async read(): Promise<{ done: boolean; value?: Uint8Array }> {
          while (queue.length === 0) await new Promise<void>((r) => (waiter = r));
          const next = queue.shift() as Uint8Array | 'end' | Error;
          if (next === 'end') return { done: true };
          if (next instanceof Error) throw next;
          return { done: false, value: next };
        },
        releaseLock() {},
      };
    },
  } as unknown as ReadableStream<Uint8Array>;
  return {
    body,
    push(chunk: string | Uint8Array) {
      queue.push(typeof chunk === 'string' ? enc.encode(chunk) : chunk);
      wake();
    },
    end() {
      queue.push('end');
      wake();
    },
    fail(e = new Error('network')) {
      queue.push(e);
      wake();
    },
  };
}

export const sseFrame = (frame: { cursor: number }, kind: 'patch' | 'snapshot') =>
  `id: ${frame.cursor}\nevent: ${kind}\ndata: ${JSON.stringify(frame)}\n\n`;

