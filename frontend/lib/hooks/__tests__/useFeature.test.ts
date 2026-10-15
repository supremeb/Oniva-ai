import { act, renderHook } from '@testing-library/react';
import { useAuthStore } from '@/lib/stores/auth';
import {
  ENGINE_CHAT_FLAG,
  ENGINE_DEEP_WORK_FLAG,
  useEngineChatMode,
  useEngineDeepWorkMode,
  useFeature,
} from '../useFeature';

const setFeatures = (features?: Record<string, boolean>) =>
  act(() => {
    useAuthStore.setState({ user: features === undefined ? null : ({ features } as never) });
  });

afterEach(() => setFeatures(undefined));

describe('useFeature', () => {
  it('is false when absent or unauthenticated', () => {
    expect(renderHook(() => useFeature(ENGINE_CHAT_FLAG)).result.current).toBe(false);
    setFeatures({});
    expect(renderHook(() => useFeature(ENGINE_CHAT_FLAG)).result.current).toBe(false);
  });

  it('reads the resolved flag', () => {
    setFeatures({ [ENGINE_CHAT_FLAG]: true });
    expect(renderHook(() => useFeature(ENGINE_CHAT_FLAG)).result.current).toBe(true);
  });
});

describe('useEngineChatMode', () => {
  it('is legacy when the flag is off', () => {
    setFeatures({ [ENGINE_CHAT_FLAG]: false });
    expect(renderHook(() => useEngineChatMode()).result.current).toBe('legacy');
  });

  it('is engine when the flag is on', () => {
    setFeatures({ [ENGINE_CHAT_FLAG]: true });
    expect(renderHook(() => useEngineChatMode()).result.current).toBe('engine');
  });

  it('keeps its mode for the mount when the flag flips, and picks up the change on remount', () => {
    setFeatures({ [ENGINE_CHAT_FLAG]: false });
    const first = renderHook(() => useEngineChatMode());
    setFeatures({ [ENGINE_CHAT_FLAG]: true });
    first.rerender();
    expect(first.result.current).toBe('legacy');
    expect(renderHook(() => useEngineChatMode()).result.current).toBe('engine');
  });
});

describe('useEngineDeepWorkMode', () => {
  it('is legacy when absent or off, independent of the chat flag', () => {
    setFeatures({ [ENGINE_CHAT_FLAG]: true });
    expect(renderHook(() => useEngineDeepWorkMode()).result.current).toBe('legacy');
    setFeatures({ [ENGINE_DEEP_WORK_FLAG]: false });
    expect(renderHook(() => useEngineDeepWorkMode()).result.current).toBe('legacy');
  });

  it('is engine when on, and keeps its mode for the mount', () => {
    setFeatures({ [ENGINE_DEEP_WORK_FLAG]: true });
    const first = renderHook(() => useEngineDeepWorkMode());
    expect(first.result.current).toBe('engine');
    setFeatures({ [ENGINE_DEEP_WORK_FLAG]: false });
    first.rerender();
    expect(first.result.current).toBe('engine');
    expect(renderHook(() => useEngineDeepWorkMode()).result.current).toBe('legacy');
  });
});
