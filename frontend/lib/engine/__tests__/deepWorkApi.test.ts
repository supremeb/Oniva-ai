import { clarifyEngine, planEngine, startEngineRun } from '../deepWorkApi';
import { apiFetch } from '@/lib/api';

jest.mock('@/lib/api', () => ({ apiFetch: jest.fn().mockResolvedValue({}) }));

const sent = () => JSON.parse((apiFetch as jest.Mock).mock.calls.at(-1)[1].body);
const CHAT = '8cbf54f7-806e-42d6-bf65-3c34cd3f57be';

describe('deepWorkApi chat_id', () => {
  beforeEach(() => (apiFetch as jest.Mock).mockClear());

  it('sends a real conversation id as chat_id', async () => {
    await clarifyEngine({ query: 'q', projectId: 'p', chatId: CHAT });
    expect(sent().chat_id).toBe(CHAT);
  });

  it('omits a local draft key: a brand-new chat has no server conversation yet', async () => {
    await clarifyEngine({ query: 'q', projectId: 'p', chatId: 'engine-dw-abc123' });
    expect(sent()).not.toHaveProperty('chat_id');
    await planEngine({ query: 'q', projectId: 'p', chatId: 'engine-dw-abc123' });
    expect(sent()).not.toHaveProperty('chat_id');
    await startEngineRun({ sessionId: 's', query: 'q', chatId: 'engine-dw-abc123' });
    expect(sent()).not.toHaveProperty('chat_id');
  });
});
