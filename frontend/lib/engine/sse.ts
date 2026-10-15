/** Server-sent-events reader over a fetch body, with a carry-over buffer. */

export interface SseEvent {
  event: string;
  data: string;
  id: string | null;
}

/**
 * Incremental parser. `push` accepts arbitrary text chunks (split anywhere,
 * including mid-line); complete events are returned, the remainder carried.
 * `onComment` fires for `:` lines (heartbeats).
 */
export class SseParser {
  private buf = '';
  private event = '';
  private data: string[] = [];
  private id: string | null = null;
  private lastId: string | null = null;
  private skipLF = false;

  constructor(private readonly onComment?: (text: string) => void) {}

  get lastEventId(): string | null {
    return this.lastId;
  }

  push(chunk: string): SseEvent[] {
    if (this.skipLF && chunk.startsWith('\n')) chunk = chunk.slice(1);
    if (chunk.length > 0) this.skipLF = false;
    this.buf += chunk;
    const out: SseEvent[] = [];
    for (;;) {
      const m = /\r\n|\n|\r/.exec(this.buf);
      if (!m) break;
      // A trailing lone \r may be the first half of \r\n: wait for more.
      if (m[0] === '\r' && m.index + 1 === this.buf.length) {
        const line = this.buf.slice(0, m.index);
        this.buf = '';
        this.skipLF = true;
        this.line(line, out);
        break;
      }
      const line = this.buf.slice(0, m.index);
      this.buf = this.buf.slice(m.index + m[0].length);
      this.line(line, out);
    }
    return out;
  }

  private line(line: string, out: SseEvent[]): void {
    if (line === '') {
      if (this.data.length > 0 || this.event !== '') {
        out.push({ event: this.event || 'message', data: this.data.join('\n'), id: this.id });
        if (this.id !== null) this.lastId = this.id;
      }
      this.event = '';
      this.data = [];
      this.id = null;
      return;
    }
    if (line.startsWith(':')) {
      this.onComment?.(line.slice(1).trimStart());
      return;
    }
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') this.event = value;
    else if (field === 'data') this.data.push(value);
    else if (field === 'id' && !value.includes('\0')) this.id = value;
  }
}

/**
 * Read `body` to the end, calling `onEvent` per event and `onActivity` for
 * every received chunk (events, comments, anything). Decoding is streaming so
 * multibyte characters split across chunks survive.
 */
export async function readSse(
  body: ReadableStream<Uint8Array>,
  onEvent: (e: SseEvent) => void,
  onActivity?: () => void
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder('utf-8');
  const parser = new SseParser(() => onActivity?.());
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      onActivity?.();
      for (const e of parser.push(decoder.decode(value, { stream: true }))) onEvent(e);
    }
    const tail = decoder.decode();
    if (tail) for (const e of parser.push(tail)) onEvent(e);
  } finally {
    reader.releaseLock();
  }
}
