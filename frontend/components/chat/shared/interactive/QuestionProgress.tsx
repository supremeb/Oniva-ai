'use client';

import { memo, useEffect, useState, useRef } from 'react';
import { cn } from '@/lib/utils';
import { Clock, WarningCircle } from '@phosphor-icons/react';
import { transitions } from '@/lib/animation-utils';

export interface QuestionProgressProps {
  /** Total timeout in seconds */
  timeout: number;
  /** Remaining time in seconds */
  remaining: number;
  /** Whether countdown is paused */
  paused?: boolean;
  /** Urgency threshold (seconds remaining) */
  urgencyThreshold?: number;
  /** Show as ring/circular progress */
  variant?: 'linear' | 'ring' | 'text';
  /** Size for ring variant */
  size?: 'sm' | 'md' | 'lg';
  /** Callback when time expires */
  onExpire?: () => void;
  /** Additional CSS classes */
  className?: string;
}

const sizeConfig = {
  sm: { ring: 24, stroke: 2 },
  md: { ring: 32, stroke: 3 },
  lg: { ring: 40, stroke: 4 },
};

/**
 * QuestionProgress - Timeout countdown indicator
 *
 * Features:
 * - Linear bar or ring progress
 * - Urgency pulse at low time (<10s by default)
 * - Color transition (normal → warning → danger)
 */
export const QuestionProgress = memo(function QuestionProgress({
  timeout,
  remaining,
  paused = false,
  urgencyThreshold = 10,
  variant = 'linear',
  size = 'md',
  onExpire,
  className,
}: QuestionProgressProps) {
  const [isUrgent, setIsUrgent] = useState(false);
  const expireCalledRef = useRef(false);

  // Calculate progress
  const progress = Math.max(0, Math.min(1, remaining / timeout));
  const progressPercent = progress * 100;

  // Determine urgency state
  useEffect(() => {
    setIsUrgent(remaining <= urgencyThreshold && remaining > 0);
  }, [remaining, urgencyThreshold]);

  // Handle expiration
  useEffect(() => {
    if (remaining <= 0 && !expireCalledRef.current) {
      expireCalledRef.current = true;
      onExpire?.();
    }
  }, [remaining, onExpire]);

  // Get color based on progress
  const getColorClass = () => {
    if (remaining <= 5) return 'text-red-500';
    if (remaining <= urgencyThreshold) return 'text-orange-500';
    if (progress <= 0.3) return 'text-yellow-500';
    return 'text-primary';
  };

  const getBgColorClass = () => {
    if (remaining <= 5) return 'bg-red-500';
    if (remaining <= urgencyThreshold) return 'bg-orange-500';
    if (progress <= 0.3) return 'bg-yellow-500';
    return 'bg-primary';
  };

  // Format time display
  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    if (mins > 0) {
      return `${mins}:${secs.toString().padStart(2, '0')}`;
    }
    return `${secs}s`;
  };

  if (variant === 'ring') {
    const config = sizeConfig[size];
    const radius = (config.ring - config.stroke) / 2;
    const circumference = 2 * Math.PI * radius;
    const strokeDashoffset = circumference * (1 - progress);

    return (
      <div
        className={cn(
          'relative inline-flex items-center justify-center',
          isUrgent && 'animate-pulse',
          className
        )}
        style={{ width: config.ring, height: config.ring }}
      >
        <svg
          className="transform -rotate-90"
          width={config.ring}
          height={config.ring}
        >
          {/* Background ring */}
          <circle
            cx={config.ring / 2}
            cy={config.ring / 2}
            r={radius}
            fill="none"
            stroke="currentColor"
            strokeWidth={config.stroke}
            className="text-muted/30"
          />
          {/* Progress ring */}
          <circle
            cx={config.ring / 2}
            cy={config.ring / 2}
            r={radius}
            fill="none"
            stroke="currentColor"
            strokeWidth={config.stroke}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={strokeDashoffset}
            className={cn(getColorClass(), transitions.colors)}
            style={{ transition: 'stroke-dashoffset 0.5s ease' }}
          />
        </svg>

        {/* Center text */}
        <span
          className={cn(
            'absolute text-xs font-mono font-medium',
            getColorClass()
          )}
        >
          {remaining}
        </span>
      </div>
    );
  }

  if (variant === 'text') {
    return (
      <div
        className={cn(
          'flex items-center gap-1.5',
          isUrgent && 'animate-pulse',
          className
        )}
      >
        {remaining <= urgencyThreshold ? (
          <WarningCircle className={cn('h-4 w-4', getColorClass())} weight="fill" />
        ) : (
          <Clock className={cn('h-4 w-4', getColorClass())} />
        )}
        <span
          className={cn(
            'text-sm font-mono font-medium tabular-nums',
            getColorClass(),
            transitions.colors
          )}
        >
          {formatTime(remaining)}
        </span>
        {paused && (
          <span className="text-xs text-muted-foreground">(paused)</span>
        )}
      </div>
    );
  }

  // Linear variant (default)
  return (
    <div className={cn('space-y-1', className)}>
      {/* Label row */}
      <div className="flex items-center justify-between text-xs">
        <div
          className={cn(
            'flex items-center gap-1',
            isUrgent && 'animate-pulse'
          )}
        >
          {remaining <= urgencyThreshold ? (
            <WarningCircle className={cn('h-3.5 w-3.5', getColorClass())} weight="fill" />
          ) : (
            <Clock className={cn('h-3.5 w-3.5', getColorClass())} />
          )}
          <span className={cn('font-medium', getColorClass())}>
            {formatTime(remaining)} remaining
          </span>
        </div>
        {paused && (
          <span className="text-muted-foreground">Paused</span>
        )}
      </div>

      {/* Progress bar */}
      <div className="h-1.5 w-full rounded-full bg-muted/50 overflow-hidden">
        <div
          className={cn(
            'h-full rounded-full transition-all duration-500 ease-out',
            getBgColorClass(),
            isUrgent && 'animate-pulse'
          )}
          style={{ width: `${progressPercent}%` }}
        />
      </div>
    </div>
  );
});

export type { QuestionProgressProps as QuestionProgressComponentProps };
