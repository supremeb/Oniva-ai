/** U12: questions on the wire (parser default, client route, error mapping). */
import questionViews from '../__fixtures__/question_views.json';
import chatViews from '../__fixtures__/chat_views.json';
import { parseConversationView, parseWatchFrame } from '../types';
import { toDecisionError } from '../approval';
import { EngineConversationClient } from '../client';

jest.mock('@/lib/api', () => {
  class APIError extends Error {
    constructor(message: string, public status?: number) {
      super(message);
    }
  }
  return { API_BASE_URL: 'http://api.test', APIError, apiFetch: jest.fn(async () => ({})), attemptTokenRefresh: jest.fn() };
});
import { apiFetch } from '@/lib/api';

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

describe('parseConversationView questions', () => {
  it('keeps the questions document of a recorded view', () => {
    const v = parseConversationView(clone((questionViews as any).question_pending));
    expect(v.questions.items).toHaveLength(1);
    expect(v.questions.items[0]).toMatchObject({ status: 'pending', multiSelect: true, allowFreeText: true });
  });

  it('defaults to no items when an older engine omits it (view and snapshot frame)', () => {
    const raw = clone((chatViews as any).chat_text);
    expect('questions' in raw).toBe(false);
    expect(parseConversationView(raw).questions).toEqual({ items: [] });
    const frame = parseWatchFrame({ cursor: 1, snapshot: raw }) as any;
    expect(frame.snapshot.questions).toEqual({ items: [] });
  });

  it('rejects a malformed questions member', () => {
    const raw = clone((questionViews as any).question_pending);
    raw.questions = { items: 'x' };
    expect(() => parseConversationView(raw)).toThrow('view.questions');
  });
});

describe('EngineConversationClient.answer', () => {
  it('POSTs { answer } to the question route with encoded ids', async () => {
    await new EngineConversationClient().answer('c 1', 't/1', { optionIds: ['a'], text: 'hi' });
    expect(apiFetch).toHaveBeenCalledWith('/api/v1/engine/conversations/c%201/questions/t%2F1/answer', {
      method: 'POST',
      body: JSON.stringify({ answer: { optionIds: ['a'], text: 'hi' } }),
    });
  });
});

describe('toDecisionError for answers', () => {
  it('maps a rejected answer to a retryable, specific message', () => {
    expect(toDecisionError({ status: 400 })).toMatchObject({ retryable: true, message: expect.stringContaining('not accepted') });
    expect(toDecisionError({ status: 422 })).toMatchObject({ retryable: true });
  });
});
