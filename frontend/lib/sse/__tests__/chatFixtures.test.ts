/**
 * Characterization of the legacy chat stream pipeline (plan 2026-10-15-001, U4):
 * recorded backend SSE events -> parseChatSSEStream -> useStreamingParts must
 * reproduce the persisted `message_parts` of the golden fixtures. This is the
 * oracle U6's shared part builders are checked against.
 *
 * Fixtures are copies of backend/tests/contracts/fixtures; refresh with
 * `node scripts/sync-sse-fixtures.mjs`. The drift test below fails if they diverge.
 */
import { TextEncoder, TextDecoder } from 'util';
(global as any).TextEncoder ??= TextEncoder;
(global as any).TextDecoder ??= TextDecoder;

import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import { renderHook, act } from '@testing-library/react';
import { parseChatSSEStream } from '../parseChatSSE';
import { useStreamingParts } from '@/hooks/useStreamingParts';

import chatText from '../__fixtures__/chat_text.json';
import chatToolCall from '../__fixtures__/chat_tool_call.json';
import chatError from '../__fixtures__/chat_error.json';

type Fixture = { events: Array<{ event: string; id: string; data: unknown }>; persisted_messages: any[] };

function readerFor(fx: Fixture) {
    const enc = new TextEncoder();
    const chunks = fx.events.map((e) =>
        enc.encode(`id: ${e.id}\nevent: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`),
    );
    let i = 0;
    return {
        read: async () => (i < chunks.length ? { done: false, value: chunks[i++] } : { done: true, value: undefined }),
    } as unknown as ReadableStreamDefaultReader<Uint8Array>;
}

const FIXTURE_NAMES = ['chat_text', 'chat_tool_call', 'chat_error'];

describe('chat SSE fixtures', () => {
    it('are in sync with backend/tests/contracts/fixtures (drift guard)', () => {
        const backendDir = join(__dirname, '..', '..', '..', '..', 'backend', 'tests', 'contracts', 'fixtures');
        if (!existsSync(backendDir)) return; // frontend-only checkout
        for (const name of FIXTURE_NAMES) {
            const mine = JSON.parse(readFileSync(join(__dirname, '..', '__fixtures__', `${name}.json`), 'utf8'));
            const theirs = JSON.parse(readFileSync(join(backendDir, `${name}.json`), 'utf8'));
            expect(mine).toEqual(theirs);
        }
    });
});

describe('parseChatSSEStream', () => {
    it('dispatches chat_text events in order', async () => {
        const seen: string[] = [];
        await parseChatSSEStream(readerFor(chatText as Fixture), {
            onContentDelta: (d) => seen.push(`text:${d}`),
            onReasoningDelta: () => {},
            onThinkingDelta: () => {},
            onToolCallStart: () => {},
            onToolCallProgress: () => {},
            onToolCallComplete: () => {},
            onSource: () => {},
            onInfoMessage: () => {},
            onPart: () => {},
            onError: (m) => seen.push(`error:${m}`),
            onDone: (d) => seen.push(`done:${d.model}`),
        });
        expect(seen.join('|')).toBe('text:Hello|text:, |text:world.|done:contract-test-model');
    });

    it('dispatches chat_error: partial text then error', async () => {
        const seen: string[] = [];
        await parseChatSSEStream(readerFor(chatError as Fixture), {
            onContentDelta: (d) => seen.push(`text:${d}`),
            onReasoningDelta: () => {},
            onThinkingDelta: () => {},
            onToolCallStart: () => {},
            onToolCallProgress: () => {},
            onToolCallComplete: () => {},
            onSource: () => {},
            onInfoMessage: () => {},
            onPart: () => {},
            onError: (m) => seen.push(`error:${m}`),
            onDone: () => seen.push('done'),
        });
        expect(seen[0].startsWith('text:')).toBe(true);
        expect(seen[seen.length - 1].startsWith('error:')).toBe(true);
        expect(seen).not.toContain('done');
    });
});

async function replay(fx: Fixture) {
    const { result } = renderHook(() => useStreamingParts());
    await act(async () => {
        result.current.reset();
        await parseChatSSEStream(readerFor(fx), {
            ...result.current.handlers,
            onError: () => {},
            onDone: (d: any) => {
                result.current.handlers.onDone?.(d);
                result.current.finalize();
            },
        } as any);
    });
    return result.current.getSnapshot();
}

/** Compare parts ignoring the normalized duration placeholder. */
function strip(parts: any[]) {
    return JSON.parse(JSON.stringify(parts, (k, v) => (k === 'durationMs' ? undefined : v)));
}

describe('useStreamingParts (legacy client-side part building)', () => {
    it('chat_text: replayed events produce the persisted message_parts', async () => {
        const snapshot = await replay(chatText as Fixture);
        const final = snapshot.richParts.map((p: any) => (p.type === 'text' ? { ...p, isStreaming: false } : p));
        expect(strip(final)).toEqual(strip((chatText as Fixture).persisted_messages[0].message_parts));
    });

    it('chat_tool_call: builds thinking, tool_call, file and text parts (client differs from persisted parts)', async () => {
        const snapshot = await replay(chatToolCall as Fixture);
        const parts = snapshot.richParts as any[];
        expect(parts.map((p) => [p.type, p.toolName ?? p.filename ?? p.title ?? '', p.status ?? ''])).toEqual([
            ['thinking', '', ''],
            ['tool_call', 'web_search', 'success'],
            ['tool_call', 'save_report', 'success'],
            ['text', '', ''],
            ['source', 'Oniva', ''],
            ['file', 'report.md', ''],
        ]);
        expect(snapshot.textContent).toBe('Done: see the report.');
    });
});
