'use client';

import { TrendUp, TrendDown, Minus } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';

interface DeltaIndicatorProps {
  delta: number | undefined | null;
  className?: string;
  /** Icon size variant */
  size?: 'sm' | 'md';
}

/**
 * Displays a delta value with appropriate trend indicator icon.
 * - Positive delta (>1%): Green with up arrow
 * - Negative delta (<-1%): Red with down arrow
 * - Neutral (-1% to 1%): Gray with dash
 */
export function DeltaIndicator({ delta, className, size = 'md' }: DeltaIndicatorProps) {
  if (delta === undefined || delta === null) return null;

  const iconSize = size === 'sm' ? 'h-3 w-3' : 'h-4 w-4';
  const textSize = size === 'sm' ? 'text-xs' : 'text-sm';

  if (delta > 0.01) {
    return (
      <span className={cn('flex items-center gap-0.5 text-green-600', textSize, className)}>
        <TrendUp className={iconSize} />
        +{(delta * 100).toFixed(1)}%
      </span>
    );
  }

  if (delta < -0.01) {
    return (
      <span className={cn('flex items-center gap-0.5 text-red-600', textSize, className)}>
        <TrendDown className={iconSize} />
        {(delta * 100).toFixed(1)}%
      </span>
    );
  }

  return (
    <span className={cn('flex items-center gap-0.5 text-muted-foreground', textSize, className)}>
      <Minus className={iconSize} />
      No change
    </span>
  );
}
