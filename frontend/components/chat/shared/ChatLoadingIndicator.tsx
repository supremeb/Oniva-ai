'use client';

import { memo } from 'react';
import { cn } from '@/lib/utils';
import { Robot, CircleNotch, Brain, Lightning } from '@phosphor-icons/react';
import { GenerativeO, GenerativeOText } from '@/components/ui/GenerativeO';

export interface ChatLoadingIndicatorProps {
  /** Loading message to display */
  message?: string;
  /** Variant style */
  variant?: 'dots' | 'spinner' | 'message' | 'thinking' | 'generative';
  /** Additional CSS classes */
  className?: string;
  /** Show avatar with loading indicator */
  showAvatar?: boolean;
  /** Compact mode */
  compact?: boolean;
}

// Animated thinking dots
const ThinkingDots = memo(function ThinkingDots() {
  return (
    <div className="flex items-center gap-1">
      <div className="h-1.5 w-1.5 bg-violet-500/70 rounded-full animate-bounce [animation-delay:-0.3s]" />
      <div className="h-1.5 w-1.5 bg-violet-500/70 rounded-full animate-bounce [animation-delay:-0.15s]" />
      <div className="h-1.5 w-1.5 bg-violet-500/70 rounded-full animate-bounce" />
    </div>
  );
});

// Pulsing brain animation for "thinking" state
const ThinkingBrain = memo(function ThinkingBrain({ message }: { message?: string }) {
  return (
    <div className="flex items-center gap-3">
      <div className="relative">
        <Brain className="h-5 w-5 text-violet-500 animate-pulse" weight="duotone" />
        <div className="absolute inset-0 bg-violet-500/20 rounded-full animate-ping" />
      </div>
      {message && (
        <span className="text-sm text-muted-foreground">{message}</span>
      )}
    </div>
  );
});

// Simple spinner with optional message
const LoadingSpinner = memo(function LoadingSpinner({
  message,
  compact,
}: {
  message?: string;
  compact?: boolean;
}) {
  return (
    <div className="flex items-center gap-2">
      <CircleNotch
        className={cn('animate-spin text-primary', compact ? 'h-4 w-4' : 'h-5 w-5')}
      />
      {message && (
        <span className={cn('text-muted-foreground', compact ? 'text-xs' : 'text-sm')}>
          {message}
        </span>
      )}
    </div>
  );
});

export const ChatLoadingIndicator = memo(function ChatLoadingIndicator({
  message = 'Thinking...',
  variant = 'message',
  className,
  showAvatar = true,
  compact = false,
}: ChatLoadingIndicatorProps) {
  // Generative variant - O with looping wave animation
  if (variant === 'generative') {
    return (
      <div className={cn('flex gap-3', className)}>
        {showAvatar && (
          <div
            className={cn(
              'flex items-center justify-center shrink-0',
              compact ? 'w-7 h-7' : 'w-9 h-9'
            )}
          >
            <GenerativeO
              size={compact ? 24 : 28}
              speed={1}
              animating={true}
            />
          </div>
        )}
        <div
          className={cn(
            'rounded-2xl rounded-bl-md bg-muted/40 border border-border/30',
            'flex items-center gap-2',
            compact ? 'px-3 py-2' : 'px-4 py-2.5'
          )}
        >
          <GenerativeOText className={compact ? 'text-sm' : 'text-base'} animating={true} />
          {message && (
            <span className={cn('text-muted-foreground', compact ? 'text-xs' : 'text-sm')}>
              {message}
            </span>
          )}
        </div>
      </div>
    );
  }

  // Thinking variant - modern AI-style indicator
  if (variant === 'thinking') {
    return (
      <div className={cn('flex gap-3', className)}>
        {showAvatar && (
          <div
            className={cn(
              'rounded-full bg-gradient-to-br from-violet-500/20 to-indigo-500/20',
              'flex items-center justify-center shrink-0 ring-1 ring-violet-500/10',
              compact ? 'w-7 h-7' : 'w-9 h-9'
            )}
          >
            <Robot
              className={cn('text-violet-600', compact ? 'h-3.5 w-3.5' : 'h-4 w-4')}
              weight="duotone"
            />
          </div>
        )}
        <div
          className={cn(
            'rounded-2xl rounded-bl-md',
            'bg-gradient-to-r from-violet-500/5 to-indigo-500/5',
            'border border-violet-500/10',
            compact ? 'px-3 py-2' : 'px-4 py-3'
          )}
        >
          <div className="flex items-center gap-3">
            <ThinkingBrain message={message} />
          </div>
        </div>
      </div>
    );
  }

  // Dots variant - simple animated dots
  if (variant === 'dots') {
    return (
      <div className={cn('flex gap-3', className)}>
        {showAvatar && (
          <div
            className={cn(
              'rounded-full bg-gradient-to-br from-violet-500/20 to-indigo-500/20',
              'flex items-center justify-center shrink-0 ring-1 ring-violet-500/10',
              compact ? 'w-7 h-7' : 'w-9 h-9'
            )}
          >
            <Robot
              className={cn('text-violet-600', compact ? 'h-3.5 w-3.5' : 'h-4 w-4')}
              weight="duotone"
            />
          </div>
        )}
        <div
          className={cn(
            'rounded-2xl rounded-bl-md bg-muted/60 border border-border/40',
            compact ? 'px-4 py-2.5' : 'px-5 py-3'
          )}
        >
          <ThinkingDots />
        </div>
      </div>
    );
  }

  // Spinner variant - centered spinner
  if (variant === 'spinner') {
    return (
      <div className={cn('flex items-center justify-center py-6', className)}>
        <LoadingSpinner message={message} compact={compact} />
      </div>
    );
  }

  // Default: message variant - looks like an incoming message
  return (
    <div className={cn('flex gap-3', className)}>
      {showAvatar && (
        <div
          className={cn(
            'rounded-full bg-gradient-to-br from-violet-500/20 to-indigo-500/20',
            'flex items-center justify-center shrink-0 ring-1 ring-violet-500/10',
            compact ? 'w-7 h-7' : 'w-9 h-9'
          )}
        >
          <Robot
            className={cn('text-violet-600', compact ? 'h-3.5 w-3.5' : 'h-4 w-4')}
            weight="duotone"
          />
        </div>
      )}
      <div
        className={cn(
          'rounded-2xl rounded-bl-md bg-muted/60 border border-border/40',
          compact ? 'px-3 py-2' : 'px-4 py-2.5'
        )}
      >
        <LoadingSpinner message={message} compact={compact} />
      </div>
    </div>
  );
});

/**
 * Inline loading indicator for within messages
 */
export const InlineLoadingIndicator = memo(function InlineLoadingIndicator({
  className,
  variant = 'default',
}: {
  className?: string;
  variant?: 'default' | 'generative';
}) {
  if (variant === 'generative') {
    return (
      <span className={cn('inline-flex items-center gap-1.5', className)}>
        <GenerativeOText className="text-sm" animating={true} />
        <span className="text-xs text-muted-foreground">Processing...</span>
      </span>
    );
  }

  return (
    <span className={cn('inline-flex items-center gap-1', className)}>
      <Lightning className="h-3 w-3 text-amber-500 animate-pulse" weight="fill" />
      <span className="text-xs text-muted-foreground">Processing...</span>
    </span>
  );
});
