'use client';

import { memo } from 'react';
import { cn } from '@/lib/utils';

export interface PulseLoaderProps {
  /** Size variant */
  size?: 'sm' | 'md' | 'lg';
  /** Color variant */
  variant?: 'default' | 'primary' | 'muted';
  /** Number of dots */
  dots?: number;
  /** Additional CSS classes */
  className?: string;
  /** Label for accessibility */
  label?: string;
}

const sizeClasses = {
  sm: 'h-1 w-1',
  md: 'h-1.5 w-1.5',
  lg: 'h-2 w-2',
};

const gapClasses = {
  sm: 'gap-0.5',
  md: 'gap-1',
  lg: 'gap-1.5',
};

const colorClasses = {
  default: 'bg-foreground/60',
  primary: 'bg-primary',
  muted: 'bg-muted-foreground/50',
};

/**
 * PulseLoader - Animated loading dots
 *
 * Classic three-dot bounce animation for loading states.
 * Each dot animates with a staggered delay.
 */
export const PulseLoader = memo(function PulseLoader({
  size = 'md',
  variant = 'default',
  dots = 3,
  className,
  label = 'Loading',
}: PulseLoaderProps) {
  const dotCount = Math.min(Math.max(dots, 2), 5);

  return (
    <div
      className={cn('flex items-center', gapClasses[size], className)}
      role="status"
      aria-label={label}
    >
      {Array.from({ length: dotCount }).map((_, index) => (
        <div
          key={index}
          className={cn(
            sizeClasses[size],
            colorClasses[variant],
            'rounded-full animate-bounce'
          )}
          style={{
            animationDelay: `${index * 150}ms`,
            animationDuration: '1s',
          }}
        />
      ))}
      <span className="sr-only">{label}</span>
    </div>
  );
});

export type { PulseLoaderProps as PulseLoaderComponentProps };
