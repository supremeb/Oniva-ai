import { renderHook, act } from '@testing-library/react';
import { useDeepWorkIntake } from '../useDeepWorkIntake';
import { useAuthStore } from '@/lib/stores/auth';
import { useDeepWorkStore } from '@/lib/stores/deep-work';
import { useProjectsStore } from '@/lib/stores/projects';

const setFlag = (on: boolean) => useAuthStore.setState({ user: { features: { ENABLE_ENGINE_DEEP_WORK: on } } } as never);
let clarify: jest.Mock;

beforeEach(() => {
  clarify = jest.fn().mockResolvedValue(undefined);
  useDeepWorkStore.getState().reset();
  useDeepWorkStore.setState({ clarify });
  useProjectsStore.setState({ projects: [], loadProjects: jest.fn(async () => {}) } as never);
});

describe('useDeepWorkIntake (legacy chat surface and engine chat share it)', () => {
  it('flag off: legacy clarify, no engine option', () => {
    setFlag(false);
    const { result } = renderHook(() => useDeepWorkIntake('p1'));
    act(() => void result.current.startIntake('q', 'chat', ['kb'], ['c']));
    expect(clarify).toHaveBeenCalledWith('q', 'chat', ['kb'], ['c']);
  });

  it('flag on: engine clarify with the page project', () => {
    setFlag(true);
    const { result } = renderHook(() => useDeepWorkIntake('p1'));
    act(() => void result.current.startIntake('q', 'chat', ['kb'], ['c']));
    expect(clarify).toHaveBeenCalledWith('q', 'chat', ['kb'], ['c'], { engine: { projectId: 'p1' } });
  });

  it('flag on without a page project: first active project, archived ones skipped', () => {
    setFlag(true);
    useProjectsStore.setState({ projects: [{ id: 'old', is_archived: true }, { id: 'p9', is_archived: false }] } as never);
    const { result } = renderHook(() => useDeepWorkIntake(undefined));
    act(() => void result.current.startIntake('q', 'chat', [], []));
    expect(clarify).toHaveBeenCalledWith('q', 'chat', [], [], { engine: { projectId: 'p9' } });
  });

  it('flag on and no project at all: shows an error instead of calling any API', () => {
    setFlag(true);
    const { result } = renderHook(() => useDeepWorkIntake(undefined));
    act(() => void result.current.startIntake('q', 'chat', [], []));
    expect(clarify).not.toHaveBeenCalled();
    expect(useDeepWorkStore.getState().status).toBe('error');
    expect(useDeepWorkStore.getState().error).toMatch(/project/i);
  });
});
