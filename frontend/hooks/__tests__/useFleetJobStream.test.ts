import { renderHook, act } from '@testing-library/react';
import { useFleetJobStream } from '../useFleetJobStream';
import { getBackgroundJob, createJobStream } from '@/lib/api/background-jobs';
import { traceToMessageParts } from '@/lib/fleet/traceToMessageParts';
import type { BackgroundJob, ExecutionTraceStep } from '@/lib/types/chat-panels';
import type { MessagePart } from '@/lib/models';
import type { ToolCallMeta } from '@/hooks/useStreamingParts';

jest.mock('@/lib/api/background-jobs', () => ({
  getBackgroundJob: jest.fn(),
  createJobStream: jest.fn(),
}));

jest.mock('@/lib/fleet/traceToMessageParts', () => ({
  traceToMessageParts: jest.fn(),
}));

const mockGetBackgroundJob = getBackgroundJob as jest.MockedFunction<typeof getBackgroundJob>;
const mockCreateJobStream = createJobStream as jest.MockedFunction<typeof createJobStream>;
const mockTraceToMessageParts = traceToMessageParts as jest.MockedFunction<typeof traceToMessageParts>;

const flushPromises = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

function makeJob(overrides: Partial<BackgroundJob> = {}): BackgroundJob {
  return {
    id: 'job-1',
    tenant_id: 'tenant-1',
    user_id: 'user-1',
    job_type: 'agent_chat',
    status: 'completed',
    progress: 100,
    execution_trace: [],
    created_at: '2026-04-03T00:00:00Z',
    ...overrides,
  };
}

function makeStep(overrides: Partial<ExecutionTraceStep> = {}): ExecutionTraceStep {
  return {
    step_id: 'step-1',
    node_id: 'tool-a',
    node_type: 'tool',
    status: 'completed',
    ...overrides,
  };
}

const emptyConversion = {
  parts: [] as MessagePart[],
  toolCallMeta: new Map<string, ToolCallMeta>(),
};

describe('useFleetJobStream', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockTraceToMessageParts.mockReturnValue(emptyConversion);
  });

  // ---------------------------------------------------------------
  // 1. Initial state
  // ---------------------------------------------------------------
  it('returns initial state before async resolution', () => {
    // Never resolve so we stay in the loading state
    mockGetBackgroundJob.mockReturnValue(new Promise(() => {}));

    const { result } = renderHook(() => useFleetJobStream('job-1'));

    expect(result.current.loading).toBe(true);
    expect(result.current.parts).toEqual([]);
    expect(result.current.isLive).toBe(false);
    expect(result.current.status).toBe('pending');
    expect(result.current.job).toBeNull();
    expect(result.current.error).toBeNull();
  });

  // ---------------------------------------------------------------
  // 2. Completed job
  // ---------------------------------------------------------------
  it('fetches a completed job and does not open SSE', async () => {
    const trace: ExecutionTraceStep[] = [makeStep()];
    const job = makeJob({ status: 'completed', execution_trace: trace });
    mockGetBackgroundJob.mockResolvedValue(job);

    const fakeParts: MessagePart[] = [{ type: 'text', content: 'done' } as MessagePart];
    const fakeMeta = new Map<string, ToolCallMeta>();
    mockTraceToMessageParts.mockReturnValue({ parts: fakeParts, toolCallMeta: fakeMeta });

    const { result } = renderHook(() => useFleetJobStream('job-1'));

    await act(async () => {
      await flushPromises();
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.isLive).toBe(false);
    expect(result.current.status).toBe('completed');
    expect(result.current.parts).toEqual(fakeParts);
    expect(result.current.job).toEqual(job);
    expect(mockCreateJobStream).not.toHaveBeenCalled();
    expect(mockTraceToMessageParts).toHaveBeenCalledWith(trace);
  });

  // ---------------------------------------------------------------
  // 3. Running job opens SSE
  // ---------------------------------------------------------------
  it('opens SSE stream for a running job', async () => {
    const job = makeJob({ status: 'running', progress: 50, execution_trace: [] });
    mockGetBackgroundJob.mockResolvedValue(job);

    const cleanupFn = jest.fn();
    mockCreateJobStream.mockReturnValue(cleanupFn);

    const { result } = renderHook(() => useFleetJobStream('job-1'));

    await act(async () => {
      await flushPromises();
    });

    expect(result.current.isLive).toBe(true);
    expect(result.current.status).toBe('running');
    expect(result.current.loading).toBe(false);
    expect(mockCreateJobStream).toHaveBeenCalledWith('job-1', expect.objectContaining({
      onStep: expect.any(Function),
      onCompleted: expect.any(Function),
      onFailed: expect.any(Function),
    }));
  });

  // ---------------------------------------------------------------
  // 4. onStep with new step_id appends to trace
  // ---------------------------------------------------------------
  it('appends a new step via onStep callback', async () => {
    const job = makeJob({ status: 'running', execution_trace: [] });
    mockGetBackgroundJob.mockResolvedValue(job);
    mockCreateJobStream.mockReturnValue(jest.fn());

    const newParts: MessagePart[] = [{ type: 'tool_call', id: 'step-new' } as MessagePart];
    // First call (initial empty trace), second call (after step added)
    mockTraceToMessageParts
      .mockReturnValueOnce(emptyConversion)
      .mockReturnValueOnce({ parts: newParts, toolCallMeta: new Map() });

    const { result } = renderHook(() => useFleetJobStream('job-1'));

    await act(async () => {
      await flushPromises();
    });

    // Capture the callbacks passed to createJobStream
    const callbacks = mockCreateJobStream.mock.calls[0][1];
    const newStep = makeStep({ step_id: 'step-new', status: 'completed' });

    await act(async () => {
      callbacks.onStep!(newStep as unknown as Record<string, unknown>);
    });

    // traceToMessageParts should have been called with the appended trace
    expect(mockTraceToMessageParts).toHaveBeenCalledTimes(2);
    const lastCallArg = mockTraceToMessageParts.mock.calls[1][0] as ExecutionTraceStep[];
    expect(lastCallArg).toHaveLength(1);
    expect(lastCallArg[0].step_id).toBe('step-new');
    expect(result.current.parts).toEqual(newParts);
  });

  // ---------------------------------------------------------------
  // 5. onStep with existing step_id updates in place
  // ---------------------------------------------------------------
  it('updates an existing step in place via onStep callback', async () => {
    const existingStep = makeStep({ step_id: 'step-1', status: 'running' });
    const job = makeJob({ status: 'running', execution_trace: [existingStep] });
    mockGetBackgroundJob.mockResolvedValue(job);
    mockCreateJobStream.mockReturnValue(jest.fn());

    const updatedParts: MessagePart[] = [{ type: 'tool_call', id: 'step-1' } as MessagePart];
    mockTraceToMessageParts
      .mockReturnValueOnce(emptyConversion)
      .mockReturnValueOnce({ parts: updatedParts, toolCallMeta: new Map() });

    const { result } = renderHook(() => useFleetJobStream('job-1'));

    await act(async () => {
      await flushPromises();
    });

    const callbacks = mockCreateJobStream.mock.calls[0][1];
    const updatedStep = makeStep({ step_id: 'step-1', status: 'completed' });

    await act(async () => {
      callbacks.onStep!(updatedStep as unknown as Record<string, unknown>);
    });

    // Trace length should stay 1 (updated, not appended)
    const lastCallArg = mockTraceToMessageParts.mock.calls[1][0] as ExecutionTraceStep[];
    expect(lastCallArg).toHaveLength(1);
    expect(lastCallArg[0].status).toBe('completed');
    expect(result.current.parts).toEqual(updatedParts);
  });

  // ---------------------------------------------------------------
  // 6. onCompleted callback
  // ---------------------------------------------------------------
  it('sets status to completed and isLive to false on onCompleted', async () => {
    const job = makeJob({ status: 'running', execution_trace: [] });
    mockGetBackgroundJob.mockResolvedValue(job);
    mockCreateJobStream.mockReturnValue(jest.fn());

    const { result } = renderHook(() => useFleetJobStream('job-1'));

    await act(async () => {
      await flushPromises();
    });

    expect(result.current.isLive).toBe(true);

    const callbacks = mockCreateJobStream.mock.calls[0][1];

    await act(async () => {
      callbacks.onCompleted!({
        job_id: 'job-1',
        status: 'completed',
        result: { answer: 'done' },
        duration_ms: 1234,
        execution_trace: [makeStep({ step_id: 'final', status: 'completed' })],
        completed_at: '2026-04-03T00:00:00Z',
      });
    });

    expect(result.current.status).toBe('completed');
    expect(result.current.isLive).toBe(false);
    expect(result.current.job?.status).toBe('completed');
    expect(result.current.job?.result).toEqual({ answer: 'done' });
    expect(result.current.job?.duration_ms).toBe(1234);
  });

  // ---------------------------------------------------------------
  // 7. onFailed callback
  // ---------------------------------------------------------------
  it('sets status to failed and error on onFailed', async () => {
    const job = makeJob({ status: 'running', execution_trace: [] });
    mockGetBackgroundJob.mockResolvedValue(job);
    mockCreateJobStream.mockReturnValue(jest.fn());

    const { result } = renderHook(() => useFleetJobStream('job-1'));

    await act(async () => {
      await flushPromises();
    });

    const callbacks = mockCreateJobStream.mock.calls[0][1];

    await act(async () => {
      callbacks.onFailed!({
        job_id: 'job-1',
        status: 'failed',
        error: 'Something broke',
        duration_ms: 500,
        execution_trace: [],
        completed_at: '2026-04-03T00:00:00Z',
      });
    });

    expect(result.current.status).toBe('failed');
    expect(result.current.isLive).toBe(false);
    expect(result.current.error).toBe('Something broke');
    expect(result.current.job?.status).toBe('failed');
  });

  // ---------------------------------------------------------------
  // 8. Cleanup on unmount
  // ---------------------------------------------------------------
  it('calls cleanup function on unmount', async () => {
    const job = makeJob({ status: 'running', execution_trace: [] });
    mockGetBackgroundJob.mockResolvedValue(job);

    const cleanupFn = jest.fn();
    mockCreateJobStream.mockReturnValue(cleanupFn);

    const { unmount } = renderHook(() => useFleetJobStream('job-1'));

    await act(async () => {
      await flushPromises();
    });

    expect(mockCreateJobStream).toHaveBeenCalled();

    unmount();

    expect(cleanupFn).toHaveBeenCalled();
  });

  // ---------------------------------------------------------------
  // Error handling: getBackgroundJob rejects
  // ---------------------------------------------------------------
  it('sets error when getBackgroundJob fails', async () => {
    mockGetBackgroundJob.mockRejectedValue(new Error('Network down'));

    const { result } = renderHook(() => useFleetJobStream('job-1'));

    await act(async () => {
      await flushPromises();
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBe('Network down');
    expect(result.current.isLive).toBe(false);
  });

  // ---------------------------------------------------------------
  // Pending job also opens SSE
  // ---------------------------------------------------------------
  it('opens SSE stream for a pending job', async () => {
    const job = makeJob({ status: 'pending', progress: 0, execution_trace: [] });
    mockGetBackgroundJob.mockResolvedValue(job);
    mockCreateJobStream.mockReturnValue(jest.fn());

    const { result } = renderHook(() => useFleetJobStream('job-1'));

    await act(async () => {
      await flushPromises();
    });

    expect(result.current.isLive).toBe(true);
    expect(mockCreateJobStream).toHaveBeenCalled();
  });
});
