'use client';

import { memo, useState, useEffect } from 'react';
import { cn } from '@/lib/utils';
import { prefersReducedMotion, getStaggerDelay } from '@/lib/animation-utils';

export interface FadeSlideProps {
  /** Content to animate */
  children: React.ReactNode;
  /** Direction of slide */
  direction?: 'up' | 'down' | 'left' | 'right';
  /** Delay in milliseconds (for staggering) */
  delay?: number;
  /** Stagger index (alternative to delay, uses 30ms per index) */
  staggerIndex?: number;
  /** Additional CSS classes */
  className?: string;
  /** Whether to trigger animation */
  show?: boolean;
  /** Duration override in milliseconds */
  duration?: number;
}

const directionClasses = {
  up: 'animate-fade-slide-up',
  down: 'animate-fade-slide-down',
  left: 'animate-fade-slide-left',
  right: 'animate-fade-slide-right',
};

/**
 * FadeSlide - Fade + slide entrance animation
 *
 * Standard entrance animation component following emilkowal timing:
 * - 150ms ease-out
 * - 8px translation
 * - Supports staggering via delay or staggerIndex
 */
export const FadeSlide = memo(function FadeSlide({
  children,
  direction = 'up',
  delay = 0,
  staggerIndex,
  className,
  show = true,
  duration,
}: FadeSlideProps) {
  const [mounted, setMounted] = useState(false);
  const reducedMotion = prefersReducedMotion();

  useEffect(() => {
    setMounted(true);
  }, []);

  // Calculate delay from staggerIndex if provided
  const animationDelay = staggerIndex !== undefined
    ? getStaggerDelay(staggerIndex).style.animationDelay
    : delay > 0 ? `${delay}ms` : undefined;

  // Don't animate if reduced motion is preferred
  if (reducedMotion || !show) {
    return <div className={className}>{children}</div>;
  }

  // Don't animate until mounted (SSR safety)
  if (!mounted) {
    return (
      <div className={cn('opacity-0', className)}>
        {children}
      </div>
    );
  }

  return (
    <div
      className={cn(
        directionClasses[direction],
        'animate-fill-both',
        className
      )}
      style={{
        animationDelay,
        ...(duration ? { animationDuration: `${duration}ms` } : {}),
      }}
    >
      {children}
    </div>
  );
});

export type { FadeSlideProps as FadeSlideComponentProps };
