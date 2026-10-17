import React from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useDeepWorkChat } from '../useDeepWorkChat';
import { useDeepWorkStore } from '@/lib/stores/deep-work';
import { useChatSessionsStore } from '@/lib/stores/chat-sessions';
import { useEngineSessionStore } from '@/components/deep-work/engineSession';
import { getDeepWorkFullState } from '@/lib/api/deep-work';

jest.mock('@/lib/api/deep-work', () => ({ getDeepWorkFullState: jest.fn() }));
jest.mock('@/lib/stores/chat-sessions', () => {
  const addMessageToSession = jest.fn();
  const state = { addMessageToSession, getSession: jest.fn(() => undefined) };
  const useChatSessionsStore = (sel: (s: typeof state) => unknown) => sel(state);
  useChatSessionsStore.getState = () => state;
  return { useChatSessionsStore };
});

const ENGINE_FS = {
  is_engine: true,
  status: 'running',
  session_id: 'dw_1',
  conversation_id: 'conv_1',
  engine_phase: 'researching',
  plan: { title: 'Battery market', steps: [] },
  todos: [],
  workspace_files: [],
  artifacts: [],
  error: null,
};
const LEGACY_V2_FS = { ...ENGINE_FS, session_id: 'legacy_1', engine_report: null, workers: [], steps: [], sources: [], connectors: [] };

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{children}</QueryClientProvider>
);

const add = () => (useChatSessionsStore.getState() as unknown as { addMessageToSession: jest.Mock }).addMessageToSession;

beforeEach(() => {
  jest.clearAllMocks();
  useEngineSessionStore.setState({ session: null, terminal: null });
  useDeepWorkStore.getState().reset?.();
});

describe('useDeepWorkChat engine rejoin', () => {
  it('publishes the engine session without hydrating the legacy store or opening SSE', async () => {
    (getDeepWorkFullState as jest.Mock).mockResolvedValue(ENGINE_FS);
    const hydrate = jest.spyOn(useDeepWorkStore.getState(), 'hydrateFromBackend');
    const ES = jest.fn();
    (globalThis as unknown as { EventSource: unknown }).EventSource = ES;

    const { result } = renderHook(() => useDeepWorkChat('chat_1'), { wrapper });
    await waitFor(() => expect(result.current.isDeepWorkForThisChat).toBe(true));
    expect(result.current.engineSession).toMatchObject({ sessionId: 'dw_1', conversationId: 'conv_1', chatId: 'chat_1', planTitle: 'Battery market' });
    expect(hydrate).not.toHaveBeenCalled();
    expect(ES).not.toHaveBeenCalled();
  });

  it('remount on a reopened chat asks full-state by the CHAT id and shows the engine card with no submit', async () => {
    (getDeepWorkFullState as jest.Mock).mockResolvedValue(ENGINE_FS);
    const ES = jest.fn();
    (globalThis as unknown as { EventSource: unknown }).EventSource = ES;
    const first = renderHook(() => useDeepWorkChat('chat_9'), { wrapper });
    await waitFor(() => expect(first.result.current.engineSession).not.toBeNull());
    first.unmount();
    expect(useEngineSessionStore.getState().session).toBeNull(); // a refresh starts empty
    const startEngineRun = jest.spyOn(useDeepWorkStore.getState(), 'approveEnginePlan');

    const again = renderHook(() => useDeepWorkChat('chat_9'), { wrapper });
    await waitFor(() => expect(again.result.current.isDeepWorkForThisChat).toBe(true));
    expect(getDeepWorkFullState).toHaveBeenLastCalledWith('chat_9');
    expect(again.result.current.engineSession).toMatchObject({ sessionId: 'dw_1', conversationId: 'conv_1', chatId: 'chat_9' });
    expect(startEngineRun).not.toHaveBeenCalled();
    expect(ES).not.toHaveBeenCalled();
  });

  it('a historical v2 session (is_engine with engine_report) opens read-only and never streams', async () => {
    (getDeepWorkFullState as jest.Mock).mockResolvedValue({ ...LEGACY_V2_FS, status: 'completed' });
    const ES = jest.fn();
    (globalThis as unknown as { EventSource: unknown }).EventSource = ES;
    const hydrate = jest.spyOn(useDeepWorkStore.getState(), 'hydrateFromBackend');
    const { result } = renderHook(() => useDeepWorkChat('chat_2'), { wrapper });
    await waitFor(() => expect(hydrate).toHaveBeenCalledTimes(1));
    expect(ES).not.toHaveBeenCalled();
    expect(result.current.engineSession).toBeNull();
    expect(useEngineSessionStore.getState().session).toBeNull();
  });

  it('a historical v1 session opens read-only with its saved summary and files, and opens no stream', async () => {
    (getDeepWorkFullState as jest.Mock).mockResolvedValue({
      ...LEGACY_V2_FS, is_engine: false, session_id: 'v1_1', status: 'running',
      summary: 'saved summary', key_findings: ['f1'], workspace_files: [{ name: 'a.md', path: 'a.md', size: 3, type: 'md', modified_at: 0 }],
    });
    const ES = jest.fn();
    (globalThis as unknown as { EventSource: unknown }).EventSource = ES;
    renderHook(() => useDeepWorkChat('chat_v1'), { wrapper });
    await waitFor(() => expect(useDeepWorkStore.getState().sessionId).toBe('v1_1'));
    const st = useDeepWorkStore.getState();
    expect(st.status).toBe('stopped'); // its executor is gone; never shown as live
    expect(st.summary).toBe('saved summary');
    expect(st.workspaceFiles.map((f) => f.path)).toEqual(['a.md']);
    expect(ES).not.toHaveBeenCalled();
  });

  it('a historical v2 session that was left running is shown as stopped, not live', async () => {
    (getDeepWorkFullState as jest.Mock).mockResolvedValue({ ...LEGACY_V2_FS, status: 'running' });
    renderHook(() => useDeepWorkChat('chat_v2'), { wrapper });
    await waitFor(() => expect(useDeepWorkStore.getState().sessionId).toBe('legacy_1'));
    expect(useDeepWorkStore.getState().status).toBe('stopped');
  });

  it('saves the result message once, only when the card watched the run end', async () => {
    (getDeepWorkFullState as jest.Mock).mockResolvedValue(ENGINE_FS);
    const { result } = renderHook(() => useDeepWorkChat('chat_1'), { wrapper });
    await waitFor(() => expect(result.current.engineSession).not.toBeNull());
    expect(add()).not.toHaveBeenCalled(); // rejoin alone saves nothing

    act(() => useEngineSessionStore.getState().setTerminal('dw_1', 'completed'));
    await waitFor(() => expect(add()).toHaveBeenCalledTimes(1));
    expect(add().mock.calls[0][1].content).toContain('Battery market');
    expect(result.current.resultMessage).not.toBeNull();

    act(() => useEngineSessionStore.getState().setTerminal('dw_1', 'completed'));
    expect(add()).toHaveBeenCalledTimes(1);
  });

  it('a cancelled run saves no message', async () => {
    (getDeepWorkFullState as jest.Mock).mockResolvedValue(ENGINE_FS);
    const { result } = renderHook(() => useDeepWorkChat('chat_1'), { wrapper });
    await waitFor(() => expect(result.current.engineSession).not.toBeNull());
    act(() => useEngineSessionStore.getState().setTerminal('dw_1', 'cancelled'));
    expect(add()).not.toHaveBeenCalled();
  });

  it('clears the engine session when the chat changes', async () => {
    (getDeepWorkFullState as jest.Mock).mockResolvedValue(ENGINE_FS);
    const { result, rerender } = renderHook(({ id }) => useDeepWorkChat(id), { wrapper, initialProps: { id: 'chat_1' } });
    await waitFor(() => expect(result.current.engineSession).not.toBeNull());
    (getDeepWorkFullState as jest.Mock).mockResolvedValue(null);
    rerender({ id: 'chat_3' });
    await waitFor(() => expect(useEngineSessionStore.getState().session).toBeNull());
    expect(result.current.isDeepWorkForThisChat).toBe(false);
  });
});
