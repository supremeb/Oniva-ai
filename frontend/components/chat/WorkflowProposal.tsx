'use client';

/**
 * WorkflowProposal — Phase 1 card shown when an agent proposes a workflow.
 *
 * Renders a card with workflow name, numbered step list, HITL warning,
 * input summary, and Execute/Cancel buttons. When the user clicks Execute,
 * it calls the workflow-execute endpoint and transitions to WorkflowProgress.
 */

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import {
  TreeStructure,
  Play,
  X,
  Warning,
  CircleNotch,
} from '@phosphor-icons/react';
import type { WorkflowPreview } from '@/lib/models';

export interface WorkflowProposalProps {
  /** The workflow preview data from SSE */
  preview: WorkflowPreview;
  /** Called when user approves execution */
  onExecute: (workflowId: string, inputs: Record<string, any>) => void;
  /** Called when user cancels */
  onCancel: () => void;
  /** Whether execution is currently starting */
  isExecuting?: boolean;
  /** Additional CSS classes */
  className?: string;
}

export const WorkflowProposal = memo(function WorkflowProposal({
  preview,
  onExecute,
  onCancel,
  isExecuting = false,
  className,
}: WorkflowProposalProps) {
  const [dismissed, setDismissed] = useState(false);

  const handleExecute = useCallback(() => {
    onExecute(preview.workflow_id, preview.input_summary);
  }, [onExecute, preview.workflow_id, preview.input_summary]);

  const handleCancel = useCallback(() => {
    setDismissed(true);
    onCancel();
  }, [onCancel]);

  if (dismissed) return null;

  const inputEntries = Object.entries(preview.input_summary).filter(
    ([, v]) => v !== null && v !== undefined && v !== '',
  );

  return (
    <div
      className={cn(
        'rounded-lg border border-neutral-200 dark:border-neutral-700/80',
        'bg-white dark:bg-neutral-900',
        'shadow-sm overflow-hidden',
        className,
      )}
    >
      {/* Header */}
      <div className="flex items-center gap-2.5 px-4 py-3 border-b border-neutral-100 dark:border-neutral-800">
        <TreeStructure
          className="h-4.5 w-4.5 text-blue-500 dark:text-blue-400 shrink-0"
          weight="duotone"
        />
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100 truncate">
            {preview.workflow_name}
          </h3>
          <p className="text-xs text-neutral-500 dark:text-neutral-400">
            {preview.steps.length} step{preview.steps.length !== 1 ? 's' : ''} — ready to execute
          </p>
        </div>
      </div>

      {/* Step list */}
      <div className="px-4 py-3 space-y-1.5">
        {preview.steps.map((step, index) => (
          <div
            key={step.node_id}
            className="flex items-center gap-2.5"
          >
            {/* Step number circle */}
            <span
              className={cn(
                'flex items-center justify-center shrink-0',
                'w-5 h-5 rounded-full text-[10px] font-medium',
                'bg-neutral-100 dark:bg-neutral-800',
                'text-neutral-500 dark:text-neutral-400',
              )}
            >
              {index + 1}
            </span>
            {/* Step label + type */}
            <span className="text-sm text-neutral-700 dark:text-neutral-300 truncate flex-1">
              {step.label}
            </span>
            <span className="text-xs text-neutral-400 dark:text-neutral-500 shrink-0">
              {step.type}
            </span>
          </div>
        ))}
      </div>

      {/* HITL warning */}
      {preview.has_hitl_nodes && (
        <div className="mx-4 mb-3 flex items-start gap-2 rounded-md bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800/50 px-3 py-2">
          <Warning
            className="h-4 w-4 text-amber-500 dark:text-amber-400 shrink-0 mt-0.5"
            weight="fill"
          />
          <p className="text-xs text-amber-700 dark:text-amber-300 leading-relaxed">
            {preview.hitl_warning ||
              'This workflow includes a human approval step \u2014 execution will halt there.'}
          </p>
        </div>
      )}

      {/* Input summary */}
      {inputEntries.length > 0 && (
        <div className="mx-4 mb-3">
          <p className="text-xs font-medium text-neutral-500 dark:text-neutral-400 mb-1.5">
            Inputs
          </p>
          <div className="rounded-md bg-neutral-50 dark:bg-neutral-800/60 border border-neutral-100 dark:border-neutral-700/50 px-3 py-2 space-y-1">
            {inputEntries.map(([key, value]) => (
              <div key={key} className="flex items-start gap-2 text-xs">
                <span className="text-neutral-500 dark:text-neutral-400 shrink-0 font-mono">
                  {key}:
                </span>
                <span className="text-neutral-700 dark:text-neutral-300 break-all">
                  {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2 px-4 py-3 border-t border-neutral-100 dark:border-neutral-800 bg-neutral-50/50 dark:bg-neutral-800/30">
        <button
          onClick={handleExecute}
          disabled={isExecuting}
          className={cn(
            'inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-md text-sm font-medium',
            'bg-blue-600 hover:bg-blue-700 text-white',
            'disabled:opacity-50 disabled:cursor-not-allowed',
            'transition-colors',
          )}
        >
          {isExecuting ? (
            <CircleNotch className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Play className="h-3.5 w-3.5" weight="fill" />
          )}
          {isExecuting ? 'Starting...' : 'Execute'}
        </button>
        <button
          onClick={handleCancel}
          disabled={isExecuting}
          className={cn(
            'inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-md text-sm font-medium',
            'bg-neutral-100 dark:bg-neutral-800 hover:bg-neutral-200 dark:hover:bg-neutral-700',
            'text-neutral-700 dark:text-neutral-300',
            'disabled:opacity-50 disabled:cursor-not-allowed',
            'transition-colors',
          )}
        >
          <X className="h-3.5 w-3.5" />
          Cancel
        </button>
      </div>
    </div>
  );
});
