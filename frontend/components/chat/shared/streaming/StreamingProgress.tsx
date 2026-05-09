'use client';

import { memo } from 'react';
import { cn } from '@/lib/utils';

export interface StreamingProgressProps {
  /** Progress value (0-100) */
  progress: number;
  /** Whether to show percentage label */
  showLabel?: boolean;
  /** Whether progress is indeterminate */
  indeterminate?: boolean;
  /** Size variant */
  size?: 'sm' | 'md' | 'lg';
  /** Color variant */
  variant?: 'default' | 'primary' | 'success' | 'warning' | 'error';
  /** Additional label text */
  label?: string;
  /** Additional CSS classes */
  className?: string;
}

const sizeClasses = {
  sm: 'h-1',
  md: 'h-1.5',
  lg: 'h-2',
};

const colorClasses = {
  default: 'bg-foreground/60',
  primary: 'bg-primary',
  success: 'bg-green-500',
  warning: 'bg-amber-500',
  error: 'bg-destructive',
};

/**
 * StreamingProgress - Linear progress bar for generation progress
 *
 * Shows progress during long-running operations.
 * Supports determinate (with percentage) and indeterminate modes.
 */
export const StreamingProgress = memo(function StreamingProgress({
  progress,
  showLabel = false,
  indeterminate = false,
  size = 'md',
  variant = 'primary',
  label,
  className,
}: StreamingProgressProps) {
  const clampedProgress = Math.min(100, Math.max(0, progress));

  return (
    <div className={cn('w-full', className)}>
      {/* Label row */}
      {(showLabel || label) && (
        <div className="flex items-center justify-between mb-1.5">
          {label && (
            <span className="text-xs text-muted-foreground">{label}</span>
          )}
          {showLabel && !indeterminate && (
            <span className="text-xs font-medium text-muted-foreground">
              {Math.round(clampedProgress)}%
            </span>
          )}
        </div>
      )}

      {/* Progress bar container */}
      <div
        className={cn(
          'w-full rounded-full bg-muted overflow-hidden',
          sizeClasses[size]
        )}
        role="progressbar"
        aria-valuenow={indeterminate ? undefined : clampedProgress}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        {/* Progress fill */}
        {indeterminate ? (
          <div
            className={cn(
              'h-full w-1/3 rounded-full',
              colorClasses[variant],
              'animate-progress-indeterminate'
            )}
          />
        ) : (
          <div
            className={cn(
              'h-full rounded-full transition-[width] duration-300 ease-out',
              colorClasses[variant]
            )}
            style={{ width: `${clampedProgress}%` }}
          />
        )}
      </div>
    </div>
  );
});

/**
 * StreamingProgressRing - Circular progress indicator
 */
export interface StreamingProgressRingProps {
  /** Progress value (0-100) */
  progress: number;
  /** Size in pixels */
  size?: number;
  /** Stroke width */
  strokeWidth?: number;
  /** Show percentage text in center */
  showLabel?: boolean;
  /** Color variant */
  variant?: 'default' | 'primary' | 'success';
  /** Additional classes */
  className?: string;
}

export const StreamingProgressRing = memo(function StreamingProgressRing({
  progress,
  size = 40,
  strokeWidth = 3,
  showLabel = false,
  variant = 'primary',
  className,
}: StreamingProgressRingProps) {
  const clampedProgress = Math.min(100, Math.max(0, progress));
  const radius = (size - strokeWidth) / 2;
  const circumference = radius * 2 * Math.PI;
  const strokeDashoffset = circumference - (clampedProgress / 100) * circumference;

  const strokeColor = {
    default: 'stroke-foreground/60',
    primary: 'stroke-primary',
    success: 'stroke-green-500',
  }[variant];

  return (
    <div className={cn('relative inline-flex items-center justify-center', className)}>
      <svg width={size} height={size} className="-rotate-90">
        {/* Background circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={strokeWidth}
          className="stroke-muted"
        />
        {/* Progress circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          className={cn(strokeColor, 'transition-[stroke-dashoffset] duration-300 ease-out')}
          style={{
            strokeDasharray: circumference,
            strokeDashoffset,
          }}
        />
      </svg>

      {/* Center label */}
      {showLabel && (
        <span className="absolute text-xs font-medium text-muted-foreground">
          {Math.round(clampedProgress)}%
        </span>
      )}
    </div>
  );
});

export type { StreamingProgressProps as StreamingProgressComponentProps };
