'use client';

import { memo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import {
  WarningCircle,
  CheckCircle,
  Warning,
  Info,
  X,
  ArrowCounterClockwise,
  Copy,
  Check,
} from '@phosphor-icons/react';
import { FadeSlide } from '../primitives/FadeSlide';
import { animations, transitions } from '@/lib/animation-utils';

export interface MessageAlertProps {
  /** Alert message */
  message: string;
  /** Alert title */
  title?: string;
  /** Additional details */
  details?: string;
  /** Dismissible */
  dismissible?: boolean;
  /** Show copy button */
  copyable?: boolean;
  /** Retry callback */
  onRetry?: () => void;
  /** Dismiss callback */
  onDismiss?: () => void;
  /** Compact mode */
  compact?: boolean;
  /** Additional CSS classes */
  className?: string;
}

/**
 * ErrorMessage - Display error alerts
 */
export const ErrorMessage = memo(function ErrorMessage({
  message,
  title = 'Error',
  details,
  dismissible = false,
  copyable = false,
  onRetry,
  onDismiss,
  compact = false,
  className,
}: MessageAlertProps) {
  const [isDismissed, setIsDismissed] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleDismiss = useCallback(() => {
    setIsDismissed(true);
    onDismiss?.();
  }, [onDismiss]);

  const handleCopy = useCallback(async () => {
    const text = details ? `${message}\n\n${details}` : message;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  }, [message, details]);

  if (isDismissed) return null;

  if (compact) {
    return (
      <FadeSlide
        className={cn(
          'flex items-center gap-2 px-3 py-2 rounded-md',
          'bg-destructive/10 border border-destructive/30 text-destructive',
          className
        )}
      >
        <WarningCircle className="h-4 w-4 shrink-0" weight="fill" />
        <span className="text-sm flex-1">{message}</span>
        {onRetry && (
          <button
            onClick={onRetry}
            className={cn(
              'text-xs underline hover:no-underline',
              transitions.colors
            )}
          >
            Retry
          </button>
        )}
      </FadeSlide>
    );
  }

  return (
    <FadeSlide
      className={cn(
        'rounded-lg border border-destructive/30 bg-destructive/5 overflow-hidden',
        'animate-shake',
        className
      )}
    >
      <div className="p-4">
        <div className="flex items-start gap-3">
          <div className="flex items-center justify-center w-8 h-8 rounded-full bg-destructive/10 shrink-0">
            <WarningCircle className="h-4 w-4 text-destructive" weight="fill" />
          </div>

          <div className="flex-1 min-w-0">
            <h4 className="font-medium text-sm text-destructive">{title}</h4>
            <p className="text-sm text-foreground mt-1">{message}</p>

            {details && (
              <pre className="mt-2 p-2 rounded bg-destructive/10 text-xs font-mono text-destructive/80 overflow-auto max-h-32">
                {details}
              </pre>
            )}

            {(onRetry || copyable) && (
              <div className="flex items-center gap-2 mt-3">
                {onRetry && (
                  <button
                    onClick={onRetry}
                    className={cn(
                      'flex items-center gap-1 px-3 py-1.5 rounded-md text-sm',
                      'bg-destructive/10 text-destructive hover:bg-destructive/20',
                      animations.press,
                      transitions.colors
                    )}
                  >
                    <ArrowCounterClockwise className="h-3.5 w-3.5" weight="bold" />
                    Retry
                  </button>
                )}
                {copyable && (
                  <button
                    onClick={handleCopy}
                    className={cn(
                      'flex items-center gap-1 px-3 py-1.5 rounded-md text-sm',
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
                        <Copy className="h-3.5 w-3.5" />
                        Copy
                      </>
                    )}
                  </button>
                )}
              </div>
            )}
          </div>

          {dismissible && (
            <button
              onClick={handleDismiss}
              className={cn(
                'p-1 rounded hover:bg-destructive/10',
                transitions.colors
              )}
            >
              <X className="h-4 w-4 text-destructive" />
            </button>
          )}
        </div>
      </div>
    </FadeSlide>
  );
});

/**
 * SuccessMessage - Display success alerts
 */
export const SuccessMessage = memo(function SuccessMessage({
  message,
  title = 'Success',
  details,
  dismissible = false,
  onDismiss,
  compact = false,
  className,
}: MessageAlertProps) {
  const [isDismissed, setIsDismissed] = useState(false);

  const handleDismiss = useCallback(() => {
    setIsDismissed(true);
    onDismiss?.();
  }, [onDismiss]);

  if (isDismissed) return null;

  if (compact) {
    return (
      <FadeSlide
        className={cn(
          'flex items-center gap-2 px-3 py-2 rounded-md',
          'bg-green-500/10 border border-green-500/30 text-green-600 dark:text-green-400',
          className
        )}
      >
        <CheckCircle className="h-4 w-4 shrink-0" weight="fill" />
        <span className="text-sm flex-1">{message}</span>
      </FadeSlide>
    );
  }

  return (
    <FadeSlide
      className={cn(
        'rounded-lg border border-green-500/30 bg-green-500/5 overflow-hidden',
        className
      )}
    >
      <div className="p-4">
        <div className="flex items-start gap-3">
          <div className="flex items-center justify-center w-8 h-8 rounded-full bg-green-500/10 shrink-0">
            <CheckCircle className="h-4 w-4 text-green-500" weight="fill" />
          </div>

          <div className="flex-1 min-w-0">
            <h4 className="font-medium text-sm text-green-600 dark:text-green-400">{title}</h4>
            <p className="text-sm text-foreground mt-1">{message}</p>
            {details && (
              <p className="text-xs text-muted-foreground mt-2">{details}</p>
            )}
          </div>

          {dismissible && (
            <button
              onClick={handleDismiss}
              className={cn(
                'p-1 rounded hover:bg-green-500/10',
                transitions.colors
              )}
            >
              <X className="h-4 w-4 text-green-500" />
            </button>
          )}
        </div>
      </div>
    </FadeSlide>
  );
});

/**
 * WarningMessage - Display warning alerts
 */
export const WarningMessage = memo(function WarningMessage({
  message,
  title = 'Warning',
  details,
  dismissible = false,
  onDismiss,
  compact = false,
  className,
}: MessageAlertProps) {
  const [isDismissed, setIsDismissed] = useState(false);

  const handleDismiss = useCallback(() => {
    setIsDismissed(true);
    onDismiss?.();
  }, [onDismiss]);

  if (isDismissed) return null;

  if (compact) {
    return (
      <FadeSlide
        className={cn(
          'flex items-center gap-2 px-3 py-2 rounded-md',
          'bg-amber-500/10 border border-amber-500/30 text-amber-600 dark:text-amber-400',
          className
        )}
      >
        <Warning className="h-4 w-4 shrink-0" weight="fill" />
        <span className="text-sm flex-1">{message}</span>
      </FadeSlide>
    );
  }

  return (
    <FadeSlide
      className={cn(
        'rounded-lg border border-amber-500/30 bg-amber-500/5 overflow-hidden',
        className
      )}
    >
      <div className="p-4">
        <div className="flex items-start gap-3">
          <div className="flex items-center justify-center w-8 h-8 rounded-full bg-amber-500/10 shrink-0">
            <Warning className="h-4 w-4 text-amber-500" weight="fill" />
          </div>

          <div className="flex-1 min-w-0">
            <h4 className="font-medium text-sm text-amber-600 dark:text-amber-400">{title}</h4>
            <p className="text-sm text-foreground mt-1">{message}</p>
            {details && (
              <p className="text-xs text-muted-foreground mt-2">{details}</p>
            )}
          </div>

          {dismissible && (
            <button
              onClick={handleDismiss}
              className={cn(
                'p-1 rounded hover:bg-amber-500/10',
                transitions.colors
              )}
            >
              <X className="h-4 w-4 text-amber-500" />
            </button>
          )}
        </div>
      </div>
    </FadeSlide>
  );
});

/**
 * InfoMessage - Display info alerts
 */
export const InfoMessage = memo(function InfoMessage({
  message,
  title = 'Info',
  details,
  dismissible = false,
  onDismiss,
  compact = false,
  className,
}: MessageAlertProps) {
  const [isDismissed, setIsDismissed] = useState(false);

  const handleDismiss = useCallback(() => {
    setIsDismissed(true);
    onDismiss?.();
  }, [onDismiss]);

  if (isDismissed) return null;

  if (compact) {
    return (
      <FadeSlide
        className={cn(
          'flex items-center gap-2 px-3 py-2 rounded-md',
          'bg-blue-500/10 border border-blue-500/30 text-blue-600 dark:text-blue-400',
          className
        )}
      >
        <Info className="h-4 w-4 shrink-0" weight="fill" />
        <span className="text-sm flex-1">{message}</span>
      </FadeSlide>
    );
  }

  return (
    <FadeSlide
      className={cn(
        'rounded-lg border border-blue-500/30 bg-blue-500/5 overflow-hidden',
        className
      )}
    >
      <div className="p-4">
        <div className="flex items-start gap-3">
          <div className="flex items-center justify-center w-8 h-8 rounded-full bg-blue-500/10 shrink-0">
            <Info className="h-4 w-4 text-blue-500" weight="fill" />
          </div>

          <div className="flex-1 min-w-0">
            <h4 className="font-medium text-sm text-blue-600 dark:text-blue-400">{title}</h4>
            <p className="text-sm text-foreground mt-1">{message}</p>
            {details && (
              <p className="text-xs text-muted-foreground mt-2">{details}</p>
            )}
          </div>

          {dismissible && (
            <button
              onClick={handleDismiss}
              className={cn(
                'p-1 rounded hover:bg-blue-500/10',
                transitions.colors
              )}
            >
              <X className="h-4 w-4 text-blue-500" />
            </button>
          )}
        </div>
      </div>
    </FadeSlide>
  );
});
