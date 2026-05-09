'use client';

import { memo } from 'react';
import { cn } from '@/lib/utils';

export interface StreamingCursorProps {
  /** Cursor style variant */
  variant?: 'block' | 'line' | 'underscore';
  /** Size variant */
  size?: 'sm' | 'md' | 'lg';
  /** Color */
  color?: 'default' | 'primary' | 'muted';
  /** Whether visible (for conditional rendering) */
  visible?: boolean;
  /** Additional CSS classes */
  className?: string;
}

const sizeClasses = {
  block: {
    sm: 'w-1 h-3',
    md: 'w-1.5 h-4',
    lg: 'w-2 h-5',
  },
  line: {
    sm: 'w-0.5 h-3',
    md: 'w-0.5 h-4',
    lg: 'w-1 h-5',
  },
  underscore: {
    sm: 'w-2 h-0.5',
    md: 'w-2.5 h-0.5',
    lg: 'w-3 h-1',
  },
};

const colorClasses = {
  default: 'bg-foreground/60',
  primary: 'bg-primary',
  muted: 'bg-muted-foreground/50',
};

/**
 * StreamingCursor - Blinking cursor indicator for streaming content
 *
 * Shows a blinking cursor to indicate that content is still being generated.
 * Uses CSS animation for performance.
 */
export const StreamingCursor = memo(function StreamingCursor({
  variant = 'block',
  size = 'md',
  color = 'default',
  visible = true,
  className,
}: StreamingCursorProps) {
  if (!visible) {
    return null;
  }

  return (
    <span
      className={cn(
        'inline-block',
        sizeClasses[variant][size],
        colorClasses[color],
        variant === 'block' && 'rounded-sm',
        variant === 'line' && 'rounded-full',
        variant === 'underscore' && 'rounded-sm align-bottom mb-0.5',
        'animate-cursor-blink',
        className
      )}
      aria-hidden="true"
    />
  );
});

/**
 * TypewriterCursor - Cursor that appears at end of typewriter effect
 */
export interface TypewriterCursorProps {
  /** Whether typing is in progress */
  typing?: boolean;
  /** Additional classes */
  className?: string;
}

export const TypewriterCursor = memo(function TypewriterCursor({
  typing = false,
  className,
}: TypewriterCursorProps) {
  return (
    <span
      className={cn(
        'inline-block w-0.5 h-[1.1em] bg-foreground/70 ml-0.5 align-text-bottom',
        typing ? 'opacity-100' : 'animate-cursor-blink',
        className
      )}
      aria-hidden="true"
    />
  );
});

export type { StreamingCursorProps as StreamingCursorComponentProps };
