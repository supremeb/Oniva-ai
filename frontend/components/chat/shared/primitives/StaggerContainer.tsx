'use client';

import { memo, Children, cloneElement, isValidElement } from 'react';
import { cn } from '@/lib/utils';
import { ANIMATION_TIMING, prefersReducedMotion } from '@/lib/animation-utils';

export interface StaggerContainerProps {
  /** Children to stagger */
  children: React.ReactNode;
  /** Delay between each child in milliseconds */
  staggerDelay?: number;
  /** Base animation class to apply to children */
  animation?: 'fade-slide-up' | 'fade-in' | 'scale-in' | 'fade-slide-left' | 'fade-slide-right';
  /** Container CSS classes */
  className?: string;
  /** Whether to stagger (can be disabled) */
  enabled?: boolean;
  /** Starting delay offset in milliseconds */
  initialDelay?: number;
}

const animationClasses = {
  'fade-slide-up': 'animate-fade-slide-up',
  'fade-in': 'animate-fade-in',
  'scale-in': 'animate-scale-in',
  'fade-slide-left': 'animate-fade-slide-left',
  'fade-slide-right': 'animate-fade-slide-right',
};

/**
 * StaggerContainer - Parent wrapper for staggered child animations
 *
 * Applies animation delays to each child based on their index.
 * Default stagger delay: 30ms (emilkowal standard)
 *
 * @example
 * <StaggerContainer>
 *   <div>Item 1</div> // delay: 0ms
 *   <div>Item 2</div> // delay: 30ms
 *   <div>Item 3</div> // delay: 60ms
 * </StaggerContainer>
 */
export const StaggerContainer = memo(function StaggerContainer({
  children,
  staggerDelay = ANIMATION_TIMING.STAGGER_DELAY,
  animation = 'fade-slide-up',
  className,
  enabled = true,
  initialDelay = 0,
}: StaggerContainerProps) {
  const reducedMotion = prefersReducedMotion();
  const shouldAnimate = enabled && !reducedMotion;

  const staggeredChildren = Children.map(children, (child, index) => {
    if (!isValidElement(child)) {
      return child;
    }

    if (!shouldAnimate) {
      return child;
    }

    const delay = initialDelay + index * staggerDelay;

    // Cast child props to access className and style
    const childProps = child.props as { className?: string; style?: React.CSSProperties };

    // Clone the child with animation styles
    return cloneElement(child, {
      ...childProps,
      className: cn(
        childProps.className,
        animationClasses[animation],
        'animate-fill-both'
      ),
      style: {
        ...childProps.style,
        animationDelay: `${delay}ms`,
      },
    } as React.HTMLAttributes<HTMLElement>);
  });

  return <div className={className}>{staggeredChildren}</div>;
});

/**
 * Higher-order component for staggered items
 * Use when you need more control over individual items
 */
export interface StaggerItemProps {
  /** Content */
  children: React.ReactNode;
  /** Item index for stagger calculation */
  index: number;
  /** Stagger delay per item */
  staggerDelay?: number;
  /** Initial delay offset */
  initialDelay?: number;
  /** Animation type */
  animation?: keyof typeof animationClasses;
  /** Additional classes */
  className?: string;
}

export const StaggerItem = memo(function StaggerItem({
  children,
  index,
  staggerDelay = ANIMATION_TIMING.STAGGER_DELAY,
  initialDelay = 0,
  animation = 'fade-slide-up',
  className,
}: StaggerItemProps) {
  const reducedMotion = prefersReducedMotion();

  if (reducedMotion) {
    return <div className={className}>{children}</div>;
  }

  const delay = initialDelay + index * staggerDelay;

  return (
    <div
      className={cn(animationClasses[animation], 'animate-fill-both', className)}
      style={{ animationDelay: `${delay}ms` }}
    >
      {children}
    </div>
  );
});

export type { StaggerContainerProps as StaggerContainerComponentProps };
