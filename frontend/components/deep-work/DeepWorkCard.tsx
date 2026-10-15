'use client';

import { memo, useMemo, useRef, useState, useCallback } from 'react';
import {
  CheckCircle,
  Circle,
  Stop,
  DownloadSimple,
  CaretDown,
  CaretUp,
  CaretRight,
  WarningCircle,
  ArrowClockwise,
} from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import {
  useDeepWorkStore,
  selectIsDeepWorkActive,
  selectEngineMode,
  selectEngineWorkers,
  selectEngineReport,
  selectCurrentActivity,
} from '@/lib/stores/deep-work';
import { useShallow } from 'zustand/react/shallow';
import { PlanCard } from './PlanCard';
import { WorkerActivityCard } from './WorkerActivityCard';
import { ChainOfThought } from '@/components/chat/shared/ChainOfThought';
import type { MessagePart } from '@/lib/models';
import type { EngineWorkerState } from '@/lib/types/deep-work';
import { FilePreview } from '@/components/chat/shared/data/FilePreview';
import ReactMarkdown from 'react-markdown';
import { DeepWorkEngineCard } from './DeepWorkEngineCard';
import { useEngineSessionStore } from './engineSession';

/**
 * DeepWorkCard — unified Perplexity-style card for deep work.
 *
 * Single component with internal state machine:
 *   planning → awaiting_approval → executing → completed
 *                                            → error
 */
const DeepWorkLegacyCard = memo(function DeepWorkLegacyCard() {
  const {
    status,
    plan,
    todos,
    error,
    engineElapsedSeconds,
    intakeResult,
    enginePhase,
  } = useDeepWorkStore(useShallow((s) => ({
    status: s.status,
    plan: s.plan,
    todos: s.todos,
    error: s.error,
    engineElapsedSeconds: s.engineElapsedSeconds,
    intakeResult: s.intakeResult,
    enginePhase: s.enginePhase,
  })));

  const engineMode = useDeepWorkStore(selectEngineMode);
  const isActive = useDeepWorkStore(selectIsDeepWorkActive);
  const engineWorkers = useDeepWorkStore(selectEngineWorkers);
  const engineReport = useDeepWorkStore(selectEngineReport);
  const currentActivity = useDeepWorkStore(selectCurrentActivity);
  const artifacts = useDeepWorkStore((s) => s.artifacts);
  const approveEnginePlan = useDeepWorkStore((s) => s.approveEnginePlan);
  const rejectEnginePlan = useDeepWorkStore((s) => s.rejectEnginePlan);
  const cancelEngineSession = useDeepWorkStore((s) => s.cancelEngineSession);
  const generatePlan = useDeepWorkStore((s) => s.generatePlan);
  const enginePendingQuery = useDeepWorkStore((s) => s.enginePendingQuery);
  const skipClarification = useDeepWorkStore((s) => s.skipClarification);
  const engineRawPlan = useDeepWorkStore((s) => s.engineRawPlan);
  const setDeliverable = useDeepWorkStore((s) => s.setDeliverable);
  const planFailure = useDeepWorkStore((s) => s.planFailure);

  // Intake answer tracking (local state — not persisted)
  const [intakeAnswers, setIntakeAnswers] = useState<Record<number, string>>({});
  const [freeTextInputs, setFreeTextInputs] = useState<Record<number, string>>({});

  // Derived counts from store
  const sourceCount = useMemo(
    () => Object.values(engineWorkers).reduce((sum, w) => sum + w.learning_count, 0),
    [engineWorkers],
  );
  const completedTodos = useMemo(
    () => todos.filter((t) => t.status === 'completed').length,
    [todos],
  );

  // Sorted workers list for rendering worker cards
  const sortedWorkers = useMemo(() => {
    const workers = Object.values(engineWorkers) as EngineWorkerState[];
    // Active workers first, then completed, then error, then idle
    const order: Record<string, number> = { researching: 0, idle: 1, complete: 2, error: 3 };
    return workers.sort((a, b) => (order[a.status] ?? 4) - (order[b.status] ?? 4));
  }, [engineWorkers]);

  // Progress bar — must be above early returns (Rules of Hooks)
  const progressRef = useRef(0);
  const progress = useMemo(() => {
    if (todos.length === 0) return 0;
    let raw: number;
    if (status === 'completed') {
      raw = 100;
    } else if (enginePhase === 'synthesizing') {
      raw = 92;
    } else if (enginePhase === 'reflecting') {
      raw = 87;
    } else {
      // Workers occupy 0–85%
      raw = Math.max(Math.round((completedTodos / todos.length) * 85), 3);
    }
    // Clamp: progress never decreases (prevents regression during gap-filling)
    progressRef.current = Math.max(progressRef.current, raw);
    return progressRef.current;
  }, [todos.length, completedTodos, enginePhase, status]);

  // Executing card starts expanded
  const [executingExpanded, setExecutingExpanded] = useState(true);

  // Report starts expanded
  const [reportExpanded, setReportExpanded] = useState(true);
  const toggleReport = useCallback(() => setReportExpanded((v) => !v), []);

  const handleDownload = useCallback(() => {
    if (!engineReport) return;
    const blob = new Blob([engineReport], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${plan?.title || 'deep-work-report'}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }, [engineReport, plan?.title]);

  const handleRetry = useCallback(() => {
    if (enginePendingQuery) {
      generatePlan(enginePendingQuery);
    }
  }, [enginePendingQuery, generatePlan]);

  const formatElapsed = useCallback((seconds: number) => {
    if (seconds < 60) return `${seconds}s`;
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
  }, []);

  const handleSubmitAnswers = useCallback(() => {
    if (!intakeResult?.questions) return;
    // Build enriched query from answers
    const answerParts = intakeResult.questions.map((q, i) => {
      const answer = intakeAnswers[i] || freeTextInputs[i] || '';
      return answer ? `${q.text} → ${answer}` : '';
    }).filter(Boolean);
    const enriched = answerParts.length > 0
      ? `${enginePendingQuery}\n\nUser clarifications:\n${answerParts.join('\n')}`
      : undefined;
    setIntakeAnswers({});
    setFreeTextInputs({});
    skipClarification(enriched);
  }, [intakeResult, intakeAnswers, freeTextInputs, enginePendingQuery, skipClarification]);

  // Don't render at all when idle
  if (!engineMode && status === 'idle') return null;
  if (status === 'idle') return null;

  // ── Clarifying state: intake questions ──
  if (status === 'clarifying' && intakeResult?.questions) {
    return (
      <div className="mt-4 mb-2">
        <div className="rounded-xl border border-neutral-200 bg-white shadow-sm overflow-hidden">
          <div className="px-5 pt-4 pb-2">
            <p className="text-sm font-medium text-neutral-900">Before I start researching...</p>
          </div>
          <div className="px-5 pb-4 space-y-4">
            {intakeResult.questions.map((q, i) => (
              <div key={i} className="space-y-2">
                <p className="text-sm text-neutral-700">{q.text}</p>
                {q.options ? (
                  <div className="flex flex-wrap gap-2">
                    {q.options.map((opt) => (
                      <button
                        key={opt}
                        onClick={() => setIntakeAnswers((prev) => ({ ...prev, [i]: opt }))}
                        className={cn(
                          'px-3 py-1.5 rounded-full text-xs font-medium border transition-colors',
                          intakeAnswers[i] === opt
                            ? 'bg-neutral-900 text-white border-neutral-900'
                            : 'bg-white text-neutral-700 border-neutral-200 hover:border-neutral-300 hover:bg-neutral-50',
                        )}
                      >
                        {opt}
                      </button>
                    ))}
                  </div>
                ) : (
                  <input
                    type="text"
                    value={freeTextInputs[i] || ''}
                    onChange={(e) => setFreeTextInputs((prev) => ({ ...prev, [i]: e.target.value }))}
                    placeholder="Type your answer..."
                    className="w-full px-3 py-2 rounded-lg border border-neutral-200 text-sm text-neutral-900 placeholder:text-neutral-400 focus:outline-none focus:ring-1 focus:ring-neutral-300"
                  />
                )}
              </div>
            ))}
            <div className="flex items-center gap-2 pt-1">
              <button
                onClick={handleSubmitAnswers}
                className="px-4 py-2 rounded-lg bg-neutral-900 text-white text-sm font-medium hover:bg-neutral-800 transition-colors"
              >
                Continue
              </button>
              <button
                onClick={() => {
                  setIntakeAnswers({});
                  setFreeTextInputs({});
                  skipClarification();
                }}
                className="px-4 py-2 rounded-lg border border-neutral-200 text-sm font-medium text-neutral-600 hover:bg-neutral-50 transition-colors"
              >
                Skip
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ── Clarifying state without questions yet: chain-of-thought ──
  if (status === 'clarifying') {
    const parts: MessagePart[] = [
      { type: 'thinking', thinkingContent: 'Analyzing query and checking available knowledge bases...', isThinking: true },
    ];
    return (
      <div className="mt-4 mb-2">
        <ChainOfThought parts={parts} animated />
      </div>
    );
  }

  // ── Planning state: chain-of-thought ──
  if (status === 'planning') {
    const parts: MessagePart[] = [
      { type: 'thinking', thinkingContent: 'Decomposing query into sub-queries and designing research plan...', isThinking: true },
    ];
    return (
      <div className="mt-4 mb-2">
        <ChainOfThought parts={parts} animated />
      </div>
    );
  }

  // ── Awaiting approval: delegate to PlanCard ──
  if (status === 'awaiting_approval' && plan) {
    return (
      <div className="mt-4 mb-2">
        <PlanCard
          title={plan.title}
          steps={plan.steps}
          status="pending"
          deliverable={(engineRawPlan?.deliverable as string | null) ?? null}
          deliverableDescription={(engineRawPlan?.deliverable_description as string | null) ?? null}
          onApprove={approveEnginePlan}
          onReject={rejectEnginePlan}
          onDeliverableChange={setDeliverable}
        />
      </div>
    );
  }

  // ── Error state ──
  if (status === 'error') {
    return (
      <div className="mt-4 mb-2">
        <div className="rounded-xl border border-red-200 bg-white shadow-sm overflow-hidden">
          <div className="px-5 py-4 flex items-start gap-3">
            <WarningCircle className="h-5 w-5 text-red-500 flex-shrink-0 mt-0.5" weight="fill" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-neutral-900">Research failed</p>
              <p className="text-sm text-neutral-500 mt-1">{error || 'An unexpected error occurred'}</p>
              {planFailure?.text && (
                <details className="mt-2">
                  <summary className="text-xs text-neutral-400 cursor-pointer select-none">What the model returned</summary>
                  <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-neutral-50 p-2 text-xs text-neutral-600">{planFailure.text}</pre>
                </details>
              )}
            </div>
            <button
              onClick={handleRetry}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-neutral-200 text-xs font-medium text-neutral-700 hover:bg-neutral-50 transition-colors flex-shrink-0"
            >
              <ArrowClockwise className="h-3.5 w-3.5" />
              Retry
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── Executing / Completed state ──
  const isExecuting = isActive && status === 'executing';
  const isCompleted = status === 'completed';

  return (
    <div className="mt-4 mb-2">
      {/* ── Executing: title + sources + checklist + activity + progress ── */}
      {isExecuting && (
        <div className="rounded-xl border border-neutral-200 bg-white shadow-sm overflow-hidden">
          {/* Header — always visible, clickable to toggle */}
          <div
            role="button"
            tabIndex={0}
            onClick={() => setExecutingExpanded((v) => !v)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setExecutingExpanded((v) => !v); } }}
            className="px-5 pt-4 pb-3 flex items-center gap-3 cursor-pointer select-none hover:bg-neutral-50/50 transition-colors"
          >
            {executingExpanded ? (
              <CaretDown className="h-3.5 w-3.5 text-neutral-400 flex-shrink-0" />
            ) : (
              <CaretRight className="h-3.5 w-3.5 text-neutral-400 flex-shrink-0" />
            )}
            <span className="text-[15px] font-medium text-neutral-900 truncate">
              {executingExpanded
                ? (plan?.title || 'Deep Work')
                : `Deep Work — ${enginePhase || 'researching'} · ${progress}%${sourceCount > 0 ? ` · ${sourceCount} source${sourceCount !== 1 ? 's' : ''}` : ''}`
              }
            </span>
            {executingExpanded && sourceCount > 0 && (
              <span className="text-sm text-neutral-400 flex-shrink-0 ml-auto">
                {sourceCount} source{sourceCount !== 1 ? 's' : ''}
              </span>
            )}
            <button
              onClick={(e) => { e.stopPropagation(); cancelEngineSession(); }}
              className="h-5 w-5 flex items-center justify-center rounded text-neutral-400 hover:text-red-500 transition-colors flex-shrink-0 ml-auto"
              title="Stop"
            >
              <Stop className="h-3.5 w-3.5" weight="fill" />
            </button>
          </div>

          {/* Collapsible body */}
          {executingExpanded && (
            <>
              {/* Per-worker activity cards */}
              {sortedWorkers.length > 0 && (
                <div className="px-5 pb-3 space-y-2">
                  {sortedWorkers.map((worker) => (
                    <WorkerActivityCard key={worker.worker_id} worker={worker} />
                  ))}
                </div>
              )}

              {/* Phase activity + progress bar */}
              <div className="px-5 pb-4 pt-1 space-y-2.5">
                {(enginePhase === 'synthesizing' || enginePhase === 'reflecting' || enginePhase === 'generating_output') && (
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 w-1.5 rounded-full bg-neutral-400 animate-pulse flex-shrink-0" />
                    <p className="text-sm text-neutral-500 truncate">
                      {enginePhase === 'generating_output'
                        ? (currentActivity || 'Generating deliverable...')
                        : enginePhase === 'synthesizing'
                        ? 'Writing report...'
                        : 'Evaluating research quality...'}
                    </p>
                  </div>
                )}

                {/* Progress bar */}
                <div className="flex items-center gap-3">
                  <div className="flex-1 h-1.5 bg-neutral-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-neutral-900 rounded-full transition-all duration-700 ease-out"
                      style={{ width: `${Math.max(progress, 3)}%` }}
                    />
                  </div>
                  <span className="text-xs text-neutral-400 flex-shrink-0 tabular-nums">
                    {completedTodos}/{todos.length}
                  </span>
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* ── Completed: artifact file cards ── */}
      {isCompleted && artifacts.length > 0 && (
        <div className="space-y-2 mb-3">
          {artifacts.map((artifact, i) => (
            <FilePreview
              key={artifact.artifactUri || i}
              filename={artifact.filename}
              size={artifact.size}
              mimeType={artifact.fileType}
              downloadUrl={artifact.downloadUrl}
              title={artifact.title}
              description={artifact.description}
              artifactType={artifact.artifactType}
              artifactUri={artifact.artifactUri}
              tags={artifact.tags}
              pageCount={artifact.pageCount ?? undefined}
              showDownload
            />
          ))}
        </div>
      )}

      {/* ── Completed: report artifact only ── */}
      {isCompleted && engineReport && (
        <div className="rounded-xl border border-neutral-200 bg-white shadow-sm overflow-hidden">
          {/* Report header */}
          <div className="flex items-center justify-between px-5 py-3 bg-neutral-50/60">
            <button
              onClick={toggleReport}
              className="flex items-center gap-2 text-sm font-medium text-neutral-900 hover:text-neutral-700 transition-colors min-w-0"
            >
              {reportExpanded ? (
                <CaretUp className="h-3.5 w-3.5 text-neutral-400 flex-shrink-0" />
              ) : (
                <CaretDown className="h-3.5 w-3.5 text-neutral-400 flex-shrink-0" />
              )}
              <span className="truncate">{plan?.title || 'Research Report'}</span>
            </button>
            <div className="flex items-center gap-2 flex-shrink-0 ml-3">
              <span className="text-xs text-neutral-400">
                {engineElapsedSeconds > 0 && formatElapsed(engineElapsedSeconds)}
                {sourceCount > 0 && ` · ${sourceCount} sources`}
              </span>
              <button
                onClick={handleDownload}
                className="flex items-center gap-1 px-2 py-1 rounded text-xs text-neutral-500 hover:text-neutral-700 hover:bg-neutral-100 transition-colors"
                title="Download as Markdown"
              >
                <DownloadSimple className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>

          {/* Report content — expanded by default */}
          {reportExpanded && (
            <div className="px-5 py-4 border-t border-neutral-100">
              <div className="prose prose-sm prose-neutral max-w-none max-h-[600px] overflow-y-auto">
                <ReactMarkdown>{engineReport}</ReactMarkdown>
              </div>
            </div>
          )}

          {/* Collapsed hint */}
          {!reportExpanded && (
            <button
              onClick={toggleReport}
              className="w-full px-5 py-2 border-t border-neutral-100 text-xs text-neutral-400 hover:text-neutral-600 hover:bg-neutral-50/50 transition-colors text-center"
            >
              Click to expand report
            </button>
          )}
        </div>
      )}
    </div>
  );
});

/**
 * Engine-runtime sessions (published by `useDeepWorkChat`) render from the
 * watch view; everything else keeps the legacy store-driven card.
 */
export const DeepWorkCard = memo(function DeepWorkCard() {
  const engineSession = useEngineSessionStore((s) => s.session);
  if (engineSession) return <DeepWorkEngineCard session={engineSession} />;
  return <DeepWorkLegacyCard />;
});
