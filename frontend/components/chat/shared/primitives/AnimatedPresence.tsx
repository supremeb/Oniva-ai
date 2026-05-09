'use client';

import { memo, useState, useEffect, useRef, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { prefersReducedMotion, ANIMATION_TIMING } from '@/lib/animation-utils';

export interface AnimatedPresenceProps {
  /** Content to animate */
  children: React.ReactNode;
  /** Whether content is visible */
  show: boolean;
  /** Additional CSS classes */
  className?: string;
  /** Animation type for entrance */
  enterAnimation?: 'fade' | 'fade-slide-up' | 'fade-slide-down' | 'scale';
  /** Animation type for exit */
  exitAnimation?: 'fade' | 'fade-slide-down' | 'fade-slide-up' | 'scale';
  /** Callback when exit animation completes */
  onExitComplete?: () => void;
  /** Unique key for remounting */
  itemKey?: string | number;
  /** Whether to unmount on exit (default: true) */
  unmountOnExit?: boolean;
}

const enterAnimationClasses = {
  'fade': 'animate-fade-in',
  'fade-slide-up': 'animate-fade-slide-up',
  'fade-slide-down': 'animate-fade-slide-down',
  'scale': 'animate-scale-in',
};

const exitAnimationClasses = {
  'fade': 'animate-fade-out',
  'fade-slide-down': 'animate-fade-slide-out-down',
  'fade-slide-up': 'animate-fade-slide-out-up',
  'scale': 'animate-scale-out',
};

/**
 * AnimatedPresence - Wrapper for enter/exit animations
 *
 * Provides CSS-based enter/exit animations with reduced motion support.
 * Uses data-state pattern for animation triggering.
 */
export const AnimatedPresence = memo(function AnimatedPresence({
  children,
  show,
  className,
  enterAnimation = 'fade-slide-up',
  exitAnimation = 'fade',
  onExitComplete,
  itemKey,
  unmountOnExit = true,
}: AnimatedPresenceProps) {
  const [shouldRender, setShouldRender] = useState(show);
  const [animationState, setAnimationState] = useState<'entering' | 'entered' | 'exiting' | 'exited'>(
    show ? 'entered' : 'exited'
  );
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);
  const reducedMotion = prefersReducedMotion();

  // Handle show prop changes
  useEffect(() => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }

    if (show) {
      setShouldRender(true);
      setAnimationState('entering');

      if (reducedMotion) {
        setAnimationState('entered');
      } else {
        timeoutRef.current = setTimeout(() => {
          setAnimationState('entered');
        }, ANIMATION_TIMING.ENTRANCE_DURATION);
      }
    } else {
      setAnimationState('exiting');

      if (reducedMotion) {
        setAnimationState('exited');
        if (unmountOnExit) {
          setShouldRender(false);
        }
        onExitComplete?.();
      } else {
        timeoutRef.current = setTimeout(() => {
          setAnimationState('exited');
          if (unmountOnExit) {
            setShouldRender(false);
          }
          onExitComplete?.();
        }, ANIMATION_TIMING.EXIT_DURATION);
      }
    }

    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, [show, reducedMotion, unmountOnExit, onExitComplete]);

  if (!shouldRender) {
    return null;
  }

  const animationClass = (() => {
    if (reducedMotion) return '';

    switch (animationState) {
      case 'entering':
        return enterAnimationClasses[enterAnimation];
      case 'exiting':
        return exitAnimationClasses[exitAnimation];
      default:
        return '';
    }
  })();

  return (
    <div
      key={itemKey}
      className={cn(animationClass, 'animate-fill-both', className)}
      data-state={animationState}
    >
      {children}
    </div>
  );
});

export type { AnimatedPresenceProps as AnimatedPresenceComponentProps };
