'use client';

/**
 * WorkflowProgress — Phase 2 card showing live workflow execution progress.
 *
 * Renders a vertical step list with real-time status updates streamed via SSE.
 * Each step shows pending/active/complete/error/halted status with appropriate
 * icons and colors. On completion shows total duration and collapsible results.
 */

import { memo, useState, useCallback, useEffect, useRef } from 'react';
import { cn } from '@/lib/utils';
import {
  TreeStructure,
  Circle,
  CheckCircle,
  XCircle,
  Pause,
  CircleNotch,
  CaretDown,
  CaretRight,
  Clock,
} from '@phosphor-icons/react';
import type { WorkflowStep, WorkflowProgressEvent, WorkflowCompleteEvent } from '@/lib/models';

export interface WorkflowProgressProps {
  /** Workflow name */
  workflowName: string;
  /** Steps from the original preview */
  steps: WorkflowStep[];
  /** Live per-step progress (stepId -> event) */
  progress: Map<string, WorkflowProgressEvent>;
  /** Final completion result (null while executing) */
  result: WorkflowCompleteEvent | null;
  /** Additional CSS classes */
  className?: string;
}

/** Format milliseconds as human-readable duration */
function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const mins = Math.floor(ms / 60000);
  const secs = ((ms % 60000) / 1000).toFixed(0);
  return `${mins}m ${secs}s`;
}

/** Step status icon */
function StepStatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'active':
      return (
        <CircleNotch
          className="h-4 w-4 text-blue-500 dark:text-blue-400 animate-spin"
          weight="bold"
        />
      );
    case 'complete':
      return (
        <CheckCircle
          className="h-4 w-4 text-green-500 dark:text-green-400"
          weight="fill"
        />
      );
    case 'error':
      return (
        <XCircle
          className="h-4 w-4 text-red-500 dark:text-red-400"
          weight="fill"
        />
      );
    case 'halted':
      return (
        <Pause
          className="h-4 w-4 text-amber-500 dark:text-amber-400"
          weight="fill"
        />
      );
    default: // pending
      return (
        <Circle
          className="h-4 w-4 text-neutral-300 dark:text-neutral-600"
          weight="regular"
        />
      );
  }
}

export const WorkflowProgress = memo(function WorkflowProgress({
  workflowName,
  steps,
  progress,
  result,
  className,
}: WorkflowProgressProps) {
  const [resultsOpen, setResultsOpen] = useState(false);
  const toggleResults = useCallback(() => setResultsOpen((v) => !v), []);

  // Elapsed time ticker
  const [elapsedMs, setElapsedMs] = useState(0);
  const startTimeRef = useRef(Date.now());

  const isComplete = result !== null;
  const hasError = Array.from(progress.values()).some((p) => p.status === 'error');
  const hasHalted = Array.from(progress.values()).some((p) => p.status === 'halted');

  useEffect(() => {
    if (isComplete) return;
    const interval = setInterval(() => {
      setElapsedMs(Date.now() - startTimeRef.current);
    }, 200);
    return () => clearInterval(interval);
  }, [isComplete]);

  // Use final elapsed if complete
  const displayElapsed = result?.elapsedMs ?? elapsedMs;

  return (
    <div
      className={cn(
        'rounded-lg border overflow-hidden',
        isComplete && !hasError
          ? 'border-green-200 dark:border-green-800/50'
          : hasError
            ? 'border-red-200 dark:border-red-800/50'
            : hasHalted
              ? 'border-amber-200 dark:border-amber-800/50'
              : 'border-blue-200 dark:border-blue-800/50',
        'bg-white dark:bg-neutral-900',
        'shadow-sm',
        className,
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2.5 px-4 py-3 border-b border-neutral-100 dark:border-neutral-800">
        <TreeStructure
          className={cn(
            'h-4.5 w-4.5 shrink-0',
            isComplete && !hasError
              ? 'text-green-500 dark:text-green-400'
              : hasError
                ? 'text-red-500 dark:text-red-400'
                : 'text-blue-500 dark:text-blue-400',
          )}
          weight="duotone"
        />
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 truncate">
            {workflowName}
          </h3>
          <p className="text-xs text-neutral-500 dark:text-neutral-400">
            {isComplete
              ? hasError
                ? 'Execution failed'
                : `Completed ${result.executedNodes}/${result.totalNodes} steps`
              : hasHalted
                ? 'Halted \u2014 waiting for human approval'
                : 'Executing...'}
          </p>
        </div>
        {/* Elapsed time */}
        <div className="flex items-center gap-1 text-xs text-neutral-400 dark:text-neutral-500 tabular-nums shrink-0">
          <Clock className="h-3 w-3" />
          {formatDuration(displayElapsed)}
        </div>
      </div>

      {/* Step list */}
      <div className="px-4 py-3">
        {steps.map((step, index) => {
          const stepProgress = progress.get(step.node_id);
          const status = stepProgress?.status ?? 'pending';
          const isLast = index === steps.length - 1;
          const stepDuration = stepProgress?.elapsedMs;

          return (
            <div key={step.node_id} className="flex gap-2.5">
              {/* Timeline rail */}
              <div className="flex flex-col items-center pt-0.5">
                <StepStatusIcon status={status} />
                {!isLast && (
                  <div
                    className={cn(
                      'w-px flex-1 my-1',
                      status === 'complete'
                        ? 'bg-green-200 dark:bg-green-800/60'
                        : status === 'error'
                          ? 'bg-red-200 dark:bg-red-800/60'
                          : 'bg-neutral-200 dark:bg-neutral-700/60',
                    )}
                  />
                )}
              </div>

              {/* Step content */}
              <div className={cn('flex-1 min-w-0', !isLast && 'pb-2.5')}>
                <div className="flex items-center gap-2">
                  <span
                    className={cn(
                      'text-sm flex-1 truncate',
                      status === 'active' && 'font-medium text-neutral-900 dark:text-neutral-100',
                      status === 'complete' && 'text-neutral-600 dark:text-neutral-300',
                      status === 'error' && 'text-red-600 dark:text-red-400',
                      status === 'halted' && 'text-amber-600 dark:text-amber-400',
                      status === 'pending' && 'text-neutral-400 dark:text-neutral-500',
                    )}
                  >
                    {step.label}
                  </span>
                  {/* Duration badge for completed steps */}
                  {status === 'complete' && stepDuration != null && (
                    <span className="text-xs text-neutral-400 dark:text-neutral-500 tabular-nums shrink-0">
                      {formatDuration(stepDuration)}
                    </span>
                  )}
                  {/* Pulse indicator for active */}
                  {status === 'active' && (
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse shrink-0" />
                  )}
                </div>

                {/* Error message */}
                {status === 'error' && stepProgress?.message && (
                  <p className="mt-0.5 text-xs text-red-500 dark:text-red-400 leading-relaxed">
                    {stepProgress.message}
                  </p>
                )}

                {/* Halted message */}
                {status === 'halted' && (
                  <p className="mt-0.5 text-xs text-amber-600 dark:text-amber-400 leading-relaxed">
                    {stepProgress?.message || 'Halted: requires human approval'}
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Completion footer with collapsible results */}
      {isComplete && !hasError && (
        <div className="border-t border-neutral-100 dark:border-neutral-800">
          <button
            onClick={toggleResults}
            className={cn(
              'flex items-center gap-2 w-full px-4 py-2.5 text-sm',
              'text-neutral-600 dark:text-neutral-300',
              'hover:bg-neutral-50 dark:hover:bg-neutral-800/50 transition-colors',
            )}
          >
            {resultsOpen ? (
              <CaretDown className="h-3.5 w-3.5 shrink-0" />
            ) : (
              <CaretRight className="h-3.5 w-3.5 shrink-0" />
            )}
            <span className="font-medium">View Results</span>
            <span className="text-xs text-neutral-400 dark:text-neutral-500 ml-auto tabular-nums">
              {formatDuration(result.elapsedMs ?? 0)} total
            </span>
          </button>
          {resultsOpen && (
            <div className="px-4 pb-3">
              <pre className="rounded-md bg-neutral-50 dark:bg-neutral-800/60 border border-neutral-100 dark:border-neutral-700/50 p-3 text-xs font-mono text-neutral-700 dark:text-neutral-300 overflow-auto max-h-64 whitespace-pre-wrap">
                {JSON.stringify(result.results, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* Error footer */}
      {isComplete && hasError && (
        <div className="px-4 py-2.5 border-t border-red-100 dark:border-red-900/30 bg-red-50/50 dark:bg-red-950/20">
          <p className="text-xs text-red-600 dark:text-red-400">
            Workflow execution failed. Check the step details above for the error.
          </p>
        </div>
      )}
    </div>
  );
});
