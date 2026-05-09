'use client';

import { memo } from 'react';
import { cn } from '@/lib/utils';

export interface SkeletonBlockProps {
  /** Width - can be number (px), string (tailwind class), or 'full' */
  width?: number | string | 'full';
  /** Height - can be number (px), string (tailwind class), or 'auto' */
  height?: number | string | 'auto';
  /** Border radius variant */
  rounded?: 'none' | 'sm' | 'md' | 'lg' | 'full';
  /** Animation variant */
  animation?: 'pulse' | 'shimmer' | 'none';
  /** Number of lines (for text skeleton) */
  lines?: number;
  /** Gap between lines */
  lineGap?: 'sm' | 'md' | 'lg';
  /** Additional CSS classes */
  className?: string;
}

const roundedClasses = {
  none: 'rounded-none',
  sm: 'rounded-sm',
  md: 'rounded-md',
  lg: 'rounded-lg',
  full: 'rounded-full',
};

const animationClasses = {
  pulse: 'animate-pulse',
  shimmer: 'animate-shimmer bg-gradient-to-r from-muted via-muted-foreground/10 to-muted bg-[length:200%_100%]',
  none: '',
};

const gapClasses = {
  sm: 'gap-1',
  md: 'gap-2',
  lg: 'gap-3',
};

/**
 * SkeletonBlock - Loading placeholder with shimmer animation
 *
 * Used for content loading states before actual content appears.
 * Supports single blocks or multiple lines for text content.
 */
export const SkeletonBlock = memo(function SkeletonBlock({
  width = 'full',
  height = 16,
  rounded = 'md',
  animation = 'shimmer',
  lines,
  lineGap = 'md',
  className,
}: SkeletonBlockProps) {
  // Calculate width style
  const widthStyle = (() => {
    if (width === 'full') return { width: '100%' };
    if (typeof width === 'number') return { width: `${width}px` };
    return {};
  })();

  // Calculate height style
  const heightStyle = (() => {
    if (height === 'auto') return {};
    if (typeof height === 'number') return { height: `${height}px` };
    return {};
  })();

  // Width class for tailwind widths
  const widthClass = typeof width === 'string' && width !== 'full' ? width : '';
  const heightClass = typeof height === 'string' && height !== 'auto' ? height : '';

  const baseClasses = cn(
    'bg-muted',
    roundedClasses[rounded],
    animationClasses[animation],
    widthClass,
    heightClass,
    className
  );

  // Render multiple lines if specified
  if (lines && lines > 1) {
    return (
      <div className={cn('flex flex-col', gapClasses[lineGap])}>
        {Array.from({ length: lines }).map((_, index) => {
          // Make last line shorter for natural text appearance
          const lineWidth = index === lines - 1 ? '75%' : '100%';

          return (
            <div
              key={index}
              className={baseClasses}
              style={{
                ...heightStyle,
                width: lineWidth,
              }}
            />
          );
        })}
      </div>
    );
  }

  return (
    <div
      className={baseClasses}
      style={{
        ...widthStyle,
        ...heightStyle,
      }}
    />
  );
});

export type { SkeletonBlockProps as SkeletonBlockComponentProps };
