/**
 * useFleetJobStream — Provides live MessagePart[] for a Fleet background job.
 *
 * For completed/failed/cancelled jobs: fetches once and converts trace to parts.
 * For running/pending jobs: opens SSE stream for real-time step updates.
 */

import { useEffect, useRef, useState, useCallback } from 'react';
import { getBackgroundJob, createJobStream } from '@/lib/api/background-jobs';
import { traceToMessageParts, type TraceConversion } from '@/lib/fleet/traceToMessageParts';
import type { BackgroundJob, ExecutionTraceStep, JobStatus } from '@/lib/types/chat-panels';
import type { MessagePart } from '@/lib/models';
import type { ToolCallMeta } from '@/hooks/useStreamingParts';

export interface FleetJobStreamState {
  parts: MessagePart[];
  toolCallMeta: Map<string, ToolCallMeta>;
  status: JobStatus;
  job: BackgroundJob | null;
  isLive: boolean;
  loading: boolean;
  error: string | null;
}

export function useFleetJobStream(jobId: string): FleetJobStreamState {
  const [parts, setParts] = useState<MessagePart[]>([]);
  const [toolCallMeta, setToolCallMeta] = useState<Map<string, ToolCallMeta>>(new Map());
  const [status, setStatus] = useState<JobStatus>('pending');
  const [job, setJob] = useState<BackgroundJob | null>(null);
  const [isLive, setIsLive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Mutable trace accumulator for SSE updates
  const traceRef = useRef<ExecutionTraceStep[]>([]);
  const cleanupRef = useRef<(() => void) | null>(null);

  const applyTrace = useCallback((trace: ExecutionTraceStep[]) => {
    const result: TraceConversion = traceToMessageParts(trace);
    setParts(result.parts);
    setToolCallMeta(result.toolCallMeta);
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function init() {
      setLoading(true);
      setError(null);

      try {
        const jobData = await getBackgroundJob(jobId);
        if (cancelled) return;

        setJob(jobData);
        setStatus(jobData.status);
        traceRef.current = jobData.execution_trace || [];
        applyTrace(traceRef.current);
        setLoading(false);

        // Only open SSE for active jobs
        if (jobData.status === 'running' || jobData.status === 'pending') {
          setIsLive(true);

          const cleanup = createJobStream(jobId, {
            onStep(stepData) {
              if (cancelled) return;
              const step = stepData as unknown as ExecutionTraceStep;
              // Update existing step or append new one
              const existingIdx = traceRef.current.findIndex(
                (s) => s.step_id === step.step_id
              );
              if (existingIdx >= 0) {
                traceRef.current[existingIdx] = step;
              } else {
                traceRef.current = [...traceRef.current, step];
              }
              applyTrace(traceRef.current);
            },
            onProgress(event) {
              if (cancelled) return;
              setStatus(event.status as JobStatus);
            },
            onCompleted(event) {
              if (cancelled) return;
              setStatus('completed');
              setIsLive(false);
              if (event.execution_trace) {
                traceRef.current = event.execution_trace;
                applyTrace(traceRef.current);
              }
              setJob((prev) =>
                prev
                  ? { ...prev, status: 'completed', result: event.result, duration_ms: event.duration_ms }
                  : prev
              );
            },
            onFailed(event) {
              if (cancelled) return;
              setStatus('failed');
              setIsLive(false);
              setError(event.error || 'Job failed');
              if (event.execution_trace) {
                traceRef.current = event.execution_trace;
                applyTrace(traceRef.current);
              }
              setJob((prev) =>
                prev ? { ...prev, status: 'failed', error: event.error } : prev
              );
            },
            onCancelled() {
              if (cancelled) return;
              setStatus('cancelled');
              setIsLive(false);
            },
            onError() {
              if (cancelled) return;
              setIsLive(false);
            },
          });

          cleanupRef.current = cleanup;
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load job');
          setLoading(false);
        }
      }
    }

    init();

    return () => {
      cancelled = true;
      cleanupRef.current?.();
      cleanupRef.current = null;
    };
  }, [jobId, applyTrace]);

  return { parts, toolCallMeta, status, job, isLive, loading, error };
}
