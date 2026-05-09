'use client';

import { memo, useState, useEffect } from 'react';
import { cn } from '@/lib/utils';
import { Brain, CaretDown, CaretRight, Lightning, Sparkle } from '@phosphor-icons/react';
import { FadeSlide } from '../primitives/FadeSlide';
import { PulseLoader } from '../primitives/PulseLoader';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { StreamingCursor } from '../streaming/StreamingCursor';
import { transitions } from '@/lib/animation-utils';

export type ThinkingVariant = 'default' | 'minimal' | 'detailed' | 'brain' | 'accordion';

export interface ThinkingIndicatorProps {
  /** Display variant */
  variant?: ThinkingVariant;
  /** Custom message */
  message?: string;
  /** Show elapsed time */
  showElapsed?: boolean;
  /** Current step/stage */
  stage?: string;
  /** Steps array for detailed variant */
  steps?: string[];
  /** Current step index */
  currentStep?: number;
  /** Streaming thinking content (for accordion variant) */
  thinkingContent?: string;
  /** Whether thinking is still streaming (for accordion variant) */
  isThinking?: boolean;
  /** Additional CSS classes */
  className?: string;
}

// Thinking Accordion - clean, minimal design matching UI inspiration
// Shows inline reasoning with auto-expand during streaming, auto-collapse when done.
// No card/border/background - just brain icon + text, collapsible.
const ThinkingAccordion = memo(function ThinkingAccordion({
  content,
  isThinking,
  elapsed,
  formatElapsed,
  className,
}: {
  content: string;
  isThinking: boolean;
  elapsed?: number;
  formatElapsed: (s: number) => string;
  className?: string;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [wasThinking, setWasThinking] = useState(false);

  // Auto-expand while thinking, auto-collapse when done
  useEffect(() => {
    if (isThinking && content) {
      setIsOpen(true);
      setWasThinking(true);
    } else if (wasThinking && !isThinking) {
      const timer = setTimeout(() => setIsOpen(false), 500);
      setWasThinking(false);
      return () => clearTimeout(timer);
    }
  }, [isThinking, content, wasThinking]);

  const triggerLabel = isThinking
    ? 'Thinking'
    : elapsed !== undefined
      ? `Thought for ${formatElapsed(elapsed)}`
      : 'View thinking';

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <CollapsibleTrigger className={cn(
        'flex items-center gap-2 text-sm w-full transition-colors',
        'text-neutral-500 hover:text-neutral-700 dark:text-neutral-400 dark:hover:text-neutral-200',
        className,
      )}>
        <Brain
          className={cn(
            'h-4 w-4 shrink-0',
            isThinking && 'animate-pulse',
          )}
          weight={isThinking ? 'fill' : 'duotone'}
        />
        <span className="flex-1 text-left font-medium">{triggerLabel}</span>
        {isOpen ? (
          <CaretDown className="h-3 w-3 shrink-0" />
        ) : (
          <CaretRight className="h-3 w-3 shrink-0" />
        )}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="py-2 pl-6">
          <div
            className="text-sm text-neutral-500 dark:text-neutral-400 whitespace-pre-wrap font-sans leading-relaxed"
            {...(isOpen ? { 'aria-live': 'polite' as const } : {})}
          >
            {content || (isThinking ? 'Starting to think...' : 'No thinking content.')}
            {isThinking && content && (
              <span className="inline-block w-1.5 h-3 bg-neutral-400 dark:bg-neutral-500 ml-0.5 animate-pulse rounded-sm" />
            )}
          </div>
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
});

const thinkingMessages = [
  'Thinking...',
  'Analyzing...',
  'Processing...',
  'Considering options...',
  'Working on it...',
];

/**
 * ThinkingIndicator - Shows AI is processing
 *
 * Features:
 * - Multiple variants
 * - Animated icons
 * - Elapsed time
 * - Stage display
 */
export const ThinkingIndicator = memo(function ThinkingIndicator({
  variant = 'default',
  message,
  showElapsed = false,
  stage,
  steps,
  currentStep = 0,
  thinkingContent,
  isThinking = false,
  className,
}: ThinkingIndicatorProps) {
  const [elapsed, setElapsed] = useState(0);
  const [messageIndex, setMessageIndex] = useState(0);

  // Elapsed time counter — stops when isThinking becomes false (for accordion)
  // or runs continuously for other variants that rely on showElapsed
  const shouldCount = showElapsed && (variant !== 'accordion' || isThinking);
  useEffect(() => {
    if (!shouldCount) return;

    const timer = setInterval(() => {
      setElapsed((prev) => prev + 1);
    }, 1000);

    return () => clearInterval(timer);
  }, [shouldCount]);

  // Rotating messages (when no custom message)
  useEffect(() => {
    if (message) return;

    const timer = setInterval(() => {
      setMessageIndex((prev) => (prev + 1) % thinkingMessages.length);
    }, 3000);

    return () => clearInterval(timer);
  }, [message]);

  const displayMessage = message || thinkingMessages[messageIndex];

  // Format elapsed time
  const formatElapsed = (seconds: number) => {
    if (seconds < 60) return `${seconds}s`;
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  if (variant === 'accordion') {
    return (
      <ThinkingAccordion
        content={thinkingContent || ''}
        isThinking={isThinking}
        elapsed={showElapsed ? elapsed : undefined}
        formatElapsed={formatElapsed}
        className={className}
      />
    );
  }

  if (variant === 'minimal') {
    return (
      <FadeSlide className={cn('flex items-center gap-2', className)}>
        <PulseLoader size="sm" />
        <span className="text-sm text-muted-foreground animate-pulse">
          {displayMessage}
        </span>
      </FadeSlide>
    );
  }

  if (variant === 'brain') {
    return (
      <FadeSlide
        className={cn(
          'flex items-center gap-3 px-4 py-3 rounded-lg',
          'bg-gradient-to-r from-primary/10 to-purple-500/10',
          'border border-primary/20',
          className
        )}
      >
        <div className="relative">
          <Brain
            className="h-6 w-6 text-primary animate-pulse"
            weight="fill"
          />
          <Sparkle
            className="absolute -top-1 -right-1 h-3 w-3 text-yellow-500 animate-bounce"
            weight="fill"
          />
        </div>
        <div className="flex-1">
          <div className="text-sm font-medium text-foreground">
            {displayMessage}
          </div>
          {stage && (
            <div className="text-xs text-muted-foreground mt-0.5">
              {stage}
            </div>
          )}
        </div>
        {showElapsed && (
          <span className="text-xs text-muted-foreground tabular-nums">
            {formatElapsed(elapsed)}
          </span>
        )}
      </FadeSlide>
    );
  }

  if (variant === 'detailed' && steps) {
    return (
      <FadeSlide
        className={cn(
          'px-4 py-3 rounded-lg border border-border bg-muted/30',
          className
        )}
      >
        <div className="flex items-center gap-3 mb-3">
          <Lightning
            className="h-5 w-5 text-primary animate-pulse"
            weight="fill"
          />
          <span className="text-sm font-medium">{displayMessage}</span>
          {showElapsed && (
            <span className="text-xs text-muted-foreground ml-auto tabular-nums">
              {formatElapsed(elapsed)}
            </span>
          )}
        </div>

        {/* Steps */}
        <div className="space-y-1.5 ml-8">
          {steps.map((step, index) => (
            <div
              key={index}
              className={cn(
                'flex items-center gap-2 text-xs',
                index < currentStep && 'text-green-500',
                index === currentStep && 'text-primary font-medium',
                index > currentStep && 'text-muted-foreground/50'
              )}
            >
              <span
                className={cn(
                  'w-4 h-4 rounded-full flex items-center justify-center text-[10px] font-medium',
                  index < currentStep && 'bg-green-500/20',
                  index === currentStep && 'bg-primary/20 animate-pulse',
                  index > currentStep && 'bg-muted'
                )}
              >
                {index + 1}
              </span>
              <span>{step}</span>
              {index === currentStep && (
                <PulseLoader size="sm" variant="primary" className="ml-1" />
              )}
            </div>
          ))}
        </div>
      </FadeSlide>
    );
  }

  // Default variant
  return (
    <FadeSlide
      className={cn(
        'flex items-center gap-3 px-4 py-3 rounded-lg',
        'border border-border bg-muted/30',
        className
      )}
    >
      <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-primary/10">
        <Lightning
          className="h-4 w-4 text-primary animate-pulse"
          weight="fill"
        />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className={cn('text-sm font-medium', transitions.colors)}>
            {displayMessage}
          </span>
          <PulseLoader size="sm" variant="primary" />
        </div>
        {stage && (
          <div className="text-xs text-muted-foreground mt-0.5">{stage}</div>
        )}
      </div>

      {showElapsed && (
        <span className="text-xs text-muted-foreground tabular-nums shrink-0">
          {formatElapsed(elapsed)}
        </span>
      )}
    </FadeSlide>
  );
});

export type { ThinkingIndicatorProps as ThinkingIndicatorComponentProps };
