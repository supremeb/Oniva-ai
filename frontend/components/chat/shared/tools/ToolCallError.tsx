'use client';

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { WarningCircle, ArrowCounterClockwise, CaretDown, CaretRight, Copy, Check } from '@phosphor-icons/react';
import { FadeSlide } from '../primitives/FadeSlide';
import { animations, transitions } from '@/lib/animation-utils';

export interface ToolCallErrorProps {
  /** Error message */
  message: string;
  /** Error code */
  code?: string;
  /** Stack trace (for debugging) */
  stackTrace?: string;
  /** Whether error is retryable */
  retryable?: boolean;
  /** Retry callback */
  onRetry?: () => void;
  /** Show shake animation */
  shake?: boolean;
  /** Additional CSS classes */
  className?: string;
}

/**
 * ToolCallError - Error state for failed tool calls
 *
 * Features:
 * - Shake animation for attention
 * - Expandable stack trace
 * - Retry button
 * - Error code badge
 */
export const ToolCallError = memo(function ToolCallError({
  message,
  code,
  stackTrace,
  retryable = true,
  onRetry,
  shake = false,
  className,
}: ToolCallErrorProps) {
  const [showStack, setShowStack] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    const fullError = stackTrace
      ? `${message}\n\nStack trace:\n${stackTrace}`
      : message;

    try {
      await navigator.clipboard.writeText(fullError);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  }, [message, stackTrace]);

  return (
    <FadeSlide
      className={cn(
        'rounded-lg border border-destructive/30 bg-destructive/5 overflow-hidden',
        shake && 'animate-shake',
        className
      )}
    >
      {/* Main error content */}
      <div className="p-3">
        <div className="flex items-start gap-3">
          {/* Error icon */}
          <div className="flex items-center justify-center w-8 h-8 rounded-full bg-destructive/10 shrink-0">
            <WarningCircle
              className="h-4 w-4 text-destructive"
              weight="fill"
            />
          </div>

          {/* Error message */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-sm font-medium text-destructive">
                Error
              </span>
              {code && (
                <span className="text-xs px-1.5 py-0.5 rounded bg-destructive/10 text-destructive font-mono">
                  {code}
                </span>
              )}
            </div>
            <p className="text-sm text-foreground">
              {message}
            </p>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 mt-3 ml-11">
          {retryable && onRetry && (
            <button
              onClick={onRetry}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm',
                'bg-destructive/10 text-destructive hover:bg-destructive/20',
                animations.press,
                transitions.colors
              )}
            >
              <ArrowCounterClockwise className="h-3.5 w-3.5" weight="bold" />
              Retry
            </button>
          )}

          <button
            onClick={handleCopy}
            className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm',
              'text-muted-foreground hover:text-foreground hover:bg-muted',
              animations.press,
              transitions.colors
            )}
          >
            {copied ? (
              <>
                <Check className="h-3.5 w-3.5 text-green-500" weight="bold" />
                <span className="text-green-500">Copied</span>
              </>
            ) : (
              <>
                <Copy className="h-3.5 w-3.5" weight="regular" />
                <span>Copy</span>
              </>
            )}
          </button>

          {stackTrace && (
            <button
              onClick={() => setShowStack((prev) => !prev)}
              className={cn(
                'flex items-center gap-1 px-2 py-1.5 rounded-md text-sm',
                'text-muted-foreground hover:text-foreground hover:bg-muted',
                transitions.colors
              )}
            >
              {showStack ? (
                <CaretDown className="h-3.5 w-3.5" />
              ) : (
                <CaretRight className="h-3.5 w-3.5" />
              )}
              <span>Stack trace</span>
            </button>
          )}
        </div>
      </div>

      {/* Stack trace (expanded) */}
      {showStack && stackTrace && (
        <div className="border-t border-destructive/20 bg-neutral-950 overflow-auto max-h-48">
          <pre className="p-3 text-xs font-mono text-neutral-400 whitespace-pre-wrap">
            {stackTrace}
          </pre>
        </div>
      )}
    </FadeSlide>
  );
});

/**
 * ToolCallErrorInline - Compact inline error display
 */
export interface ToolCallErrorInlineProps {
  message: string;
  onRetry?: () => void;
  className?: string;
}

export const ToolCallErrorInline = memo(function ToolCallErrorInline({
  message,
  onRetry,
  className,
}: ToolCallErrorInlineProps) {
  return (
    <div
      className={cn(
        'flex items-center gap-2 text-sm text-destructive',
        className
      )}
    >
      <WarningCircle className="h-4 w-4 shrink-0" weight="fill" />
      <span className="truncate">{message}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          className={cn(
            'shrink-0 underline hover:no-underline',
            transitions.colors
          )}
        >
          Retry
        </button>
      )}
    </div>
  );
});

export type { ToolCallErrorProps as ToolCallErrorComponentProps };
