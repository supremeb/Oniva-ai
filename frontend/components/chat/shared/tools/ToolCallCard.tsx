'use client';

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { CaretDown, CaretRight } from '@phosphor-icons/react';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { ToolCallHeader, type ToolCallHeaderProps } from './ToolCallHeader';
import { ToolCallProgress, type ToolCallStep } from './ToolCallProgress';
import { ToolCallResult } from './ToolCallResult';
import { ToolCallError } from './ToolCallError';
import { transitions } from '@/lib/animation-utils';

export interface ToolCallCardProps {
  /** Unique identifier */
  id: string;
  /** Tool name */
  toolName: string;
  /** Execution status */
  status: 'pending' | 'running' | 'success' | 'error';
  /** Tool arguments (input) */
  args?: Record<string, unknown>;
  /** Tool result (output) */
  result?: unknown;
  /** Error message if failed */
  error?: string;
  /** Error code */
  errorCode?: string;
  /** Execution duration in milliseconds */
  durationMs?: number;
  /** Execution steps for progress display */
  steps?: ToolCallStep[];
  /** Whether card is collapsible */
  collapsible?: boolean;
  /** Default collapsed state */
  defaultCollapsed?: boolean;
  /** Compact mode */
  compact?: boolean;
  /** Callback for retry */
  onRetry?: () => void;
  /** Additional CSS classes */
  className?: string;
}

/**
 * ToolCallCard - Complete tool call visualization
 *
 * Combines header, progress, result, and error components
 * into a unified card display.
 *
 * Features:
 * - Collapsible content
 * - Arguments preview
 * - Progress steps
 * - Result/error display
 */
export const ToolCallCard = memo(function ToolCallCard({
  id,
  toolName,
  status,
  args,
  result,
  error,
  errorCode,
  durationMs,
  steps,
  collapsible = true,
  defaultCollapsed = false,
  compact = false,
  onRetry,
  className,
}: ToolCallCardProps) {
  const [isCollapsed, setIsCollapsed] = useState(defaultCollapsed);

  const toggleCollapse = useCallback(() => {
    setIsCollapsed((prev) => !prev);
  }, []);

  const hasContent = args || steps || result !== undefined || error;
  const showCollapseToggle = collapsible && hasContent;

  return (
    <FadeSlide>
      <SpringCard
        variant="outline"
        size={compact ? 'sm' : 'md'}
        className={cn(
          'overflow-hidden',
          status === 'error' && 'border-destructive/30',
          status === 'running' && 'border-primary/30',
          className
        )}
      >
        {/* Header */}
        <div
          className={cn(
            'flex items-center gap-2',
            showCollapseToggle && 'cursor-pointer',
            transitions.colors
          )}
          onClick={showCollapseToggle ? toggleCollapse : undefined}
        >
          {/* Collapse toggle */}
          {showCollapseToggle && (
            <button
              className="p-0.5 rounded hover:bg-muted shrink-0"
              aria-expanded={!isCollapsed}
            >
              {isCollapsed ? (
                <CaretRight className="h-3.5 w-3.5 text-muted-foreground" />
              ) : (
                <CaretDown className="h-3.5 w-3.5 text-muted-foreground" />
              )}
            </button>
          )}

          {/* Tool call header */}
          <ToolCallHeader
            toolName={toolName}
            status={status}
            durationMs={durationMs}
            className="flex-1"
          />
        </div>

        {/* Expandable content */}
        {!isCollapsed && hasContent && (
          <div className={cn('mt-3 space-y-3', compact ? 'text-xs' : 'text-sm')}>
            {/* Arguments */}
            {args && Object.keys(args).length > 0 && (
              <div>
                <div className="text-xs font-medium text-muted-foreground mb-1.5">
                  Arguments
                </div>
                <div className="rounded-md bg-muted/50 p-2 font-mono text-xs overflow-auto max-h-32">
                  <code>
                    {JSON.stringify(args, null, 2)}
                  </code>
                </div>
              </div>
            )}

            {/* Progress steps */}
            {steps && steps.length > 0 && status === 'running' && (
              <div>
                <div className="text-xs font-medium text-muted-foreground mb-1.5">
                  Progress
                </div>
                <ToolCallProgress
                  steps={steps}
                  compact={compact}
                  showDetails
                />
              </div>
            )}

            {/* Result */}
            {status === 'success' && result !== undefined && (
              <div>
                <div className="text-xs font-medium text-muted-foreground mb-1.5">
                  Result
                </div>
                <ToolCallResult
                  result={result}
                  defaultCollapsed={false}
                  maxHeight={200}
                />
              </div>
            )}

            {/* Error */}
            {status === 'error' && error && (
              <ToolCallError
                message={error}
                code={errorCode}
                retryable={!!onRetry}
                onRetry={onRetry}
                shake
              />
            )}
          </div>
        )}
      </SpringCard>
    </FadeSlide>
  );
});

/**
 * ToolCallCardCompact - Minimal inline tool call display
 */
export interface ToolCallCardCompactProps {
  toolName: string;
  status: 'pending' | 'running' | 'success' | 'error';
  durationMs?: number;
  className?: string;
}

export const ToolCallCardCompact = memo(function ToolCallCardCompact({
  toolName,
  status,
  durationMs,
  className,
}: ToolCallCardCompactProps) {
  return (
    <div
      className={cn(
        'inline-flex items-center gap-2 px-2 py-1 rounded-md border border-border bg-muted/30',
        className
      )}
    >
      <ToolCallHeader
        toolName={toolName}
        status={status}
        durationMs={durationMs}
      />
    </div>
  );
});

export type { ToolCallCardProps as ToolCallCardComponentProps };
