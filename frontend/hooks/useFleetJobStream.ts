/**
 * useFleetJobStream — Provides live MessagePart[] for a Fleet background job.
 *
 * Engine jobs (the list row carries a `conversationId`): the engine conversation watch is the
 * source of truth for transcript, run tree and queued steers; the job row supplies the
 * result, the agent name and the final status (a cancel or a finished sync lands there).
 * Legacy jobs keep the trace path: completed/failed/cancelled jobs fetch once and convert the
 * trace to parts; running/pending jobs open the job SSE stream.
 */

import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { getBackgroundJob, createJobStream } from '@/lib/api/background-jobs';
import { traceToMessageParts, type TraceConversion } from '@/lib/fleet/traceToMessageParts';
import { useEngineConversation } from '@/lib/engine/useEngineConversation';
import { createViewAdapter, type EngineMessage } from '@/lib/engine/viewToMessages';
import { isRunActive } from '@/lib/engine/run';
import type { Connection } from '@/lib/engine/watcher';
import type { InboxState } from '@/lib/engine/types';
import { createEngineViewAdapter } from '@/lib/run/adapters/engineView';
import { isActive, isTerminal, normalizeStatus, type RunStatus } from '@/lib/run/status';
import type { RunNode } from '@/lib/run/tree';
import type { BackgroundJob, ExecutionTraceStep } from '@/lib/types/chat-panels';
import type { MessagePart } from '@/lib/models';
import type { ToolCallMeta } from '@/hooks/useStreamingParts';

export interface FleetJobStreamState {
  parts: MessagePart[];
  toolCallMeta: Map<string, ToolCallMeta>;
  /** Engine jobs: the transcript as chat messages (user steers and agent turns). Empty for legacy jobs. */
  messages: EngineMessage[];
  /** Engine jobs: the run tree (`RunTree` input). Empty for legacy jobs. */
  tree: RunNode[];
  /** Engine jobs: queued and placed steers from the server inbox. */
  inbox: InboxState | null;
  engine: boolean;
  connection: Connection | null;
  status: RunStatus;
  job: BackgroundJob | null;
  isLive: boolean;
  loading: boolean;
  error: string | null;
  /** Re-read the job row (after a cancel). */
  refresh: () => Promise<void>;
  /** Withdraw a queued steer (engine jobs). */
  cancelSubmission: (submissionId: string) => Promise<void>;
}

/** Re-read cadence of the job row after the engine run has ended and the row has not settled yet. */
const SETTLE_POLL_MS = 2000;

export function useFleetJobStream(jobId: string, opts: { conversationId?: string | null } = {}): FleetJobStreamState {
  const engineId = opts.conversationId ?? null;
  const engine = engineId !== null;
  const conv = useEngineConversation(engineId);
  const view = engine ? conv.view : null;

  const [parts, setParts] = useState<MessagePart[]>([]);
  const [toolCallMeta, setToolCallMeta] = useState<Map<string, ToolCallMeta>>(new Map());
  const [legacyStatus, setStatus] = useState<RunStatus>('queued');
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
    if (engine) return;
    let cancelled = false;

    async function init() {
      setLoading(true);
      setError(null);

      try {
        const jobData = await getBackgroundJob(jobId);
        if (cancelled) return;

        setJob(jobData);
        setStatus(normalizeStatus(jobData.status, 'fleet job'));
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
              setStatus(normalizeStatus(event.status, 'fleet job stream'));
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
  }, [jobId, applyTrace, engine]);

  // ---- Engine path ------------------------------------------------------
  const [engineJob, setEngineJob] = useState<BackgroundJob | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const jobIdRef = useRef(jobId);
  jobIdRef.current = jobId;

  const refresh = useCallback(async () => {
    const id = jobIdRef.current;
    try {
      const fresh = await getBackgroundJob(id);
      if (jobIdRef.current === id) {
        setEngineJob(fresh);
        setJobError(null);
      }
    } catch (err) {
      if (jobIdRef.current === id) setJobError(err instanceof Error ? err.message : 'Failed to load job');
    }
  }, []);

  useEffect(() => {
    if (!engine) return;
    setEngineJob(null);
    setJobError(null);
    void refresh();
  }, [engine, jobId, refresh]);

  const adaptMessages = useMemo(() => createViewAdapter(), [jobId]);
  const adaptTree = useMemo(() => createEngineViewAdapter(), [jobId]);
  const messages = useMemo(() => (engine && view ? adaptMessages(view) : []), [engine, view, adaptMessages]);
  const tree = useMemo(() => (engine && view ? adaptTree(view) : EMPTY_TREE), [engine, view, adaptTree]);

  const jobStatus: RunStatus | null = engineJob ? normalizeStatus(engineJob.status, 'fleet job') : null;
  const viewIdle = engine && view !== null && !isRunActive(view);
  // The job row settles a moment after the engine run ends (finished sync): re-read until it does.
  const settling = engine && jobStatus !== null && !isTerminal(jobStatus) && viewIdle;
  useEffect(() => {
    if (!settling) return;
    void refresh();
    const t = setInterval(() => void refresh(), SETTLE_POLL_MS);
    return () => clearInterval(t);
  }, [settling, refresh]);

  const engineParts = useMemo(
    () => messages.filter((m) => m.role === 'assistant').flatMap((m) => m.richParts ?? []),
    [messages]
  );

  if (!engine) {
    return {
      parts, toolCallMeta, messages: EMPTY_MESSAGES, tree: EMPTY_TREE, inbox: null, engine: false, connection: null,
      status: legacyStatus, job, isLive, loading, error, refresh: noopAsync, cancelSubmission: noopCancel,
    };
  }

  let status: RunStatus;
  if (jobStatus !== null && isTerminal(jobStatus)) status = jobStatus;
  else {
    const root = tree.find((n) => n.id.startsWith('run:'));
    status = root ? root.status : (jobStatus ?? (view ? 'running' : 'queued'));
  }

  return {
    parts: engineParts,
    toolCallMeta: EMPTY_META,
    messages,
    tree,
    inbox: view?.inbox ?? null,
    engine: true,
    connection: conv.connection,
    status,
    job: engineJob,
    isLive: isActive(status),
    loading: engineJob === null && jobError === null,
    error: jobError ?? (conv.error ? String(conv.error) : null),
    refresh,
    cancelSubmission: conv.cancelSubmission,
  };
}

const EMPTY_TREE: RunNode[] = [];
const EMPTY_MESSAGES: EngineMessage[] = [];
const EMPTY_META = new Map<string, ToolCallMeta>();
const noopAsync = async () => {};
const noopCancel = async (_id: string) => {};
