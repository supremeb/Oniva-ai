import { TextEncoder } from 'util';
import { TextDecoder } from 'util';
import { readSse, SseParser, type SseEvent } from '../sse';
import { makeBody } from '../__fixtures__/stream';

Object.assign(globalThis, { TextDecoder });

describe('SseParser', () => {
  it('parses id/event/data and carries over a split line', () => {
    const p = new SseParser();
    expect(p.push('id: 4\nevent: pat')).toEqual([]);
    expect(p.push('ch\ndata: {"a"')).toEqual([]);
    expect(p.push(':1}\n\n')).toEqual([{ id: '4', event: 'patch', data: '{"a":1}' }]);
    expect(p.lastEventId).toBe('4');
  });

  it('handles CRLF split between chunks, comments and multi-line data', () => {
    const comments: string[] = [];
    const p = new SseParser((c) => comments.push(c));
    const out = [...p.push(': ping\r'), ...p.push('\ndata: a\r\ndata: b\r\n\r'), ...p.push('\n')];
    expect(out).toEqual([{ id: null, event: 'message', data: 'a\nb' }]);
    expect(comments).toEqual(['ping']);
  });

  it('ignores a comment-only block and unknown fields', () => {
    const p = new SseParser();
    expect(p.push(': hb\n\nretry: 5\n\n')).toEqual([]);
  });
});

describe('readSse', () => {
  it('survives a multibyte character split across chunks', async () => {
    const f = makeBody();
    const bytes = new TextEncoder().encode('id: 1\nevent: patch\ndata: "wörld 🌍"\n\n');
    const cut = bytes.indexOf(0xf0) + 2; // mid 4-byte emoji
    f.push(bytes.slice(0, cut));
    f.push(bytes.slice(cut));
    f.end();
    const events: SseEvent[] = [];
    await readSse(f.body, (e) => events.push(e));
    expect(events).toEqual([{ id: '1', event: 'patch', data: '"wörld 🌍"' }]);
  });

  it('reports activity for comment pings and chunks', async () => {
    const f = makeBody();
    f.push(': ping\n\n');
    f.end();
    let n = 0;
    await readSse(f.body, () => {}, () => n++);
    expect(n).toBeGreaterThanOrEqual(1);
  });

  it('byte-at-a-time delivery yields identical events', async () => {
    const text = 'id: 2\nevent: snapshot\ndata: {"x":"é"}\n\n: ping\n\nid: 3\nevent: patch\ndata: []\n\n';
    const f = makeBody();
    for (const b of new TextEncoder().encode(text)) f.push(new Uint8Array([b]));
    f.end();
    const ev: SseEvent[] = [];
    await readSse(f.body, (e) => ev.push(e));
    expect(ev.map((e) => [e.id, e.event])).toEqual([['2', 'snapshot'], ['3', 'patch']]);
    expect(ev[0].data).toBe('{"x":"é"}');
  });
});
