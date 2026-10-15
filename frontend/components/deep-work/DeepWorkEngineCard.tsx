'use client';

import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CaretDown, CaretRight, DownloadSimple, Stop, WarningCircle } from '@phosphor-icons/react';
import ReactMarkdown from 'react-markdown';
import { ConnectionBanner } from '@/components/chat/ConnectionBanner';
import { CostBadge } from '@/components/chat/CostBadge';
import { QueuedChips, type SendingChip } from '@/components/chat/QueuedChips';
import { FilePreview } from '@/components/chat/shared/data/FilePreview';
import { useEngineDeepWork } from '@/lib/engine/useEngineDeepWork';
import { useConversationCost } from '@/lib/engine/cost';
import { CostPopover } from './CostPopover';
import { WorkerActivityCard } from './WorkerActivityCard';
import { WorkerTranscript } from './WorkerTranscript';
import { useEngineSessionStore, type EngineDeepWorkSession } from './engineSession';

const PHASE_TEXT: Record<string, string> = {
  researching: 'Researching',
  reflecting: 'Evaluating research quality...',
  synthesizing: 'Writing report...',
  generating_output: 'Generating deliverable...',
};

const WORKER_ORDER: Record<string, number> = { researching: 0, idle: 1, complete: 2, error: 3 };

function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return s % 60 ? `${m}m ${s % 60}s` : `${m}m`;
}

interface DeepWorkEngineCardProps {
  session: EngineDeepWorkSession;
}

/**
 * Deep Work card for engine-runtime sessions: a pure view of `useEngineDeepWork`
 * (one watch subscription, no EventSource, server-minted worker ids).
 */
export const DeepWorkEngineCard = memo(function DeepWorkEngineCard({ session }: DeepWorkEngineCardProps) {
  const { sessionId, conversationId } = session;
  const { conversation, view, deepWork, connection, steer, stop } = useEngineDeepWork(conversationId, {
    sessionId,
    meta: { status: session.status, error: session.error, planTitle: session.planTitle },
  });
  const { cost, active: costActive, liveTokens, refresh: refreshCost } = useConversationCost(conversationId, view);
  const setTerminal = useEngineSessionStore((s) => s.setTerminal);

  const [expanded, setExpanded] = useState(true);
  const [reportOpen, setReportOpen] = useState(true);
  const [stopping, setStopping] = useState(false);
  const [steerText, setSteerText] = useState('');
  const [sending, setSending] = useState<SendingChip[]>([]);

  const running = deepWork.status === 'executing';
  const finished = deepWork.status === 'completed' || deepWork.status === 'error' || deepWork.status === 'cancelled';

  // Report a terminal state only when this mount watched the run end, so a rejoin of a
  // finished run never re-announces its result.
  const sawRunning = useRef(false);
  useEffect(() => {
    if (view && running) sawRunning.current = true;
    if (sawRunning.current && finished && (deepWork.status === 'completed' || deepWork.status === 'error' || deepWork.status === 'cancelled')) {
      sawRunning.current = false;
      setTerminal(sessionId, deepWork.status);
    }
  }, [view, running, finished, deepWork.status, sessionId, setTerminal]);

  const workers = useMemo(
    () => [...deepWork.workers].sort((a, b) => (WORKER_ORDER[a.status] ?? 4) - (WORKER_ORDER[b.status] ?? 4)),
    [deepWork.workers],
  );
  const total = Math.max(deepWork.workers.length, deepWork.plan?.steps.length ?? 0);
  const done = deepWork.workers.filter((w) => w.status === 'complete' || w.status === 'error').length;
  const sourceCount = deepWork.sources.length;

  const progressRef = useRef(0);
  const progress = useMemo(() => {
    let raw: number;
    if (deepWork.status === 'completed') raw = 100;
    else if (deepWork.phase === 'generating_output') raw = 95;
    else if (deepWork.phase === 'synthesizing') raw = 92;
    else if (deepWork.phase === 'reflecting') raw = 87;
    else raw = total === 0 ? 0 : Math.max(Math.round((done / total) * 85), 3);
    progressRef.current = Math.max(progressRef.current, raw);
    return progressRef.current;
  }, [deepWork.status, deepWork.phase, done, total]);

  const handleStop = useCallback(async () => {
    if (stopping) return;
    setStopping(true);
    try {
      await stop();
    } catch {
      setStopping(false);
    }
  }, [stop, stopping]);

  const handleSteer = useCallback(async () => {
    const text = steerText.trim();
    if (!text) return;
    const tempId = `local:${Date.now()}:${Math.random().toString(36).slice(2)}`;
    setSteerText('');
    setSending((s) => [...s, { requestId: tempId, mode: 'steer', text }]);
    try {
      await steer(text);
      setSending((s) => s.filter((c) => c.requestId !== tempId));
    } catch {
      setSending((s) => s.map((c) => (c.requestId === tempId ? { ...c, rejected: true } : c)));
    }
  }, [steer, steerText]);

  const handleDownload = useCallback(() => {
    if (!deepWork.report) return;
    const blob = new Blob([deepWork.report], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${deepWork.plan?.title || 'deep-work-report'}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }, [deepWork.report, deepWork.plan?.title]);

  if (!view && !finished && !session.status) return null;

  const title = deepWork.plan?.title || session.planTitle || 'Deep Work';
  const stalledAfter = 45;
  const statusText = stopping && running
    ? 'Stopping...'
    : deepWork.status === 'cancelled'
      ? 'Stopped'
      : deepWork.status === 'error'
        ? 'Failed'
        : deepWork.status === 'completed'
          ? 'Complete'
          : deepWork.recovering
            ? 'Resuming after restart...'
            : deepWork.phase
              ? (PHASE_TEXT[deepWork.phase] ?? deepWork.phase)
              : 'Starting...';

  return (
    <div className="mt-4 mb-2" data-testid="deep-work-engine-card" data-status={deepWork.status}>
      <ConnectionBanner connection={connection} error={conversation.error} stalledAfterSeconds={stalledAfter} />
      <div className={`rounded-xl border bg-white shadow-sm ${deepWork.status === 'error' ? 'border-red-200' : 'border-neutral-200'}`}>
        <div className="flex items-center gap-3 px-5 pb-3 pt-4">
          <button
            type="button"
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
            className="flex min-w-0 flex-1 items-center gap-3 text-left"
          >
            {expanded ? <CaretDown className="h-3.5 w-3.5 flex-shrink-0 text-neutral-400" /> : <CaretRight className="h-3.5 w-3.5 flex-shrink-0 text-neutral-400" />}
            <span className="truncate text-[15px] font-medium text-neutral-900">{title}</span>
          </button>
          {deepWork.resumed && (
            <span data-testid="resumed-chip" className="flex-shrink-0 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs text-amber-800">
              Resumed after restart
            </span>
          )}
          <span data-testid="deep-work-status" role="status" className="flex-shrink-0 text-xs text-neutral-500">
            {statusText}
          </span>
          {sourceCount > 0 && (
            <span className="flex-shrink-0 text-sm text-neutral-400">
              {sourceCount} source{sourceCount !== 1 ? 's' : ''}
            </span>
          )}
          <span className="flex-shrink-0">
            <CostBadge variant="total" cost={cost} active={costActive} liveTokens={liveTokens} onRefresh={() => void refreshCost()} />
            {!costActive && <CostPopover cost={cost} />}
          </span>
          {running && (
            <button
              type="button"
              onClick={() => void handleStop()}
              disabled={stopping}
              aria-label="Stop"
              title="Stop"
              className="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded text-neutral-400 transition-colors hover:text-red-500 disabled:opacity-50"
            >
              <Stop className="h-3.5 w-3.5" weight="fill" />
            </button>
          )}
        </div>

        {expanded && (
          <>
            {workers.length > 0 && (
              <div className="space-y-2 px-5 pb-3">
                {workers.map((w) => (
                  <WorkerActivityCard
                    key={w.key}
                    worker={w}
                    renderDetail={w.childConversationId ? () => <WorkerTranscript sessionId={sessionId} childConversationId={w.childConversationId as string} /> : undefined}
                  />
                ))}
              </div>
            )}

            {deepWork.status === 'error' && (
              <div className="flex items-start gap-3 px-5 pb-4">
                <WarningCircle className="mt-0.5 h-5 w-5 flex-shrink-0 text-red-500" weight="fill" />
                <div>
                  <p className="text-sm font-medium text-neutral-900">Research failed</p>
                  <p className="mt-1 text-sm text-neutral-500">{deepWork.error || 'An unexpected error occurred'}</p>
                </div>
              </div>
            )}

            {running && (
              <div className="space-y-2.5 px-5 pb-4 pt-1">
                {(deepWork.phase === 'synthesizing' || deepWork.phase === 'reflecting' || deepWork.phase === 'generating_output') && (
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 w-1.5 flex-shrink-0 animate-pulse rounded-full bg-neutral-400 motion-reduce:animate-none" />
                    <p className="truncate text-sm text-neutral-500">{PHASE_TEXT[deepWork.phase]}</p>
                  </div>
                )}
                <div className="flex items-center gap-3">
                  <div
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={progress}
                    className="h-1.5 flex-1 overflow-hidden rounded-full bg-neutral-100"
                  >
                    <div className="h-full rounded-full bg-neutral-900 transition-all duration-700 ease-out motion-reduce:transition-none" style={{ width: `${Math.max(progress, 3)}%` }} />
                  </div>
                  <span className="flex-shrink-0 text-xs tabular-nums text-neutral-400">
                    {done}/{total}
                  </span>
                </div>
                <QueuedChips inbox={view?.inbox ?? null} sending={sending} onCancel={conversation.cancelSubmission} />
                <form
                  className="flex items-center gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void handleSteer();
                  }}
                >
                  <input
                    type="text"
                    value={steerText}
                    onChange={(e) => setSteerText(e.target.value)}
                    placeholder="Steer the research..."
                    aria-label="Steer the research"
                    className="flex-1 rounded-lg border border-neutral-200 px-3 py-1.5 text-sm text-neutral-900 placeholder:text-neutral-400 focus:outline-none focus:ring-1 focus:ring-neutral-300"
                  />
                  <button
                    type="submit"
                    disabled={!steerText.trim()}
                    className="rounded-lg border border-neutral-200 px-3 py-1.5 text-xs font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50"
                  >
                    Send
                  </button>
                </form>
              </div>
            )}
          </>
        )}
      </div>

      {deepWork.status === 'completed' && deepWork.artifacts.length > 0 && (
        <div className="mt-3 space-y-2">
          {deepWork.artifacts.map((a, i) => (
            <FilePreview
              key={a.artifactUri || a.filename || i}
              filename={a.filename}
              size={a.size}
              mimeType={a.fileType}
              downloadUrl={a.downloadUrl}
              title={a.title}
              description={a.description}
              artifactType={a.artifactType}
              artifactUri={a.artifactUri}
              tags={a.tags}
              pageCount={a.pageCount ?? undefined}
              showDownload
            />
          ))}
        </div>
      )}

      {deepWork.report && (
        <div className="mt-3 overflow-hidden rounded-xl border border-neutral-200 bg-white shadow-sm">
          <div className="flex items-center justify-between bg-neutral-50/60 px-5 py-3">
            <button
              type="button"
              aria-expanded={reportOpen}
              onClick={() => setReportOpen((v) => !v)}
              className="flex min-w-0 items-center gap-2 text-sm font-medium text-neutral-900"
            >
              {reportOpen ? <CaretDown className="h-3.5 w-3.5 text-neutral-400" /> : <CaretRight className="h-3.5 w-3.5 text-neutral-400" />}
              <span className="truncate">{deepWork.status === 'completed' ? title : 'Report (in progress)'}</span>
            </button>
            <div className="ml-3 flex flex-shrink-0 items-center gap-2">
              {deepWork.startedAt != null && deepWork.endedAt != null && (
                <span className="text-xs text-neutral-400">{formatElapsed(deepWork.endedAt - deepWork.startedAt)}</span>
              )}
              <button
                type="button"
                onClick={handleDownload}
                aria-label="Download as Markdown"
                title="Download as Markdown"
                className="rounded px-2 py-1 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-700"
              >
                <DownloadSimple className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
          <div aria-live="polite" data-testid="deep-work-report" hidden={!reportOpen} className="border-t border-neutral-100 px-5 py-4">
            <div className="prose prose-sm prose-neutral max-h-[600px] max-w-none overflow-y-auto">
              <ReactMarkdown>{deepWork.report}</ReactMarkdown>
            </div>
          </div>
        </div>
      )}
    </div>
  );
});
