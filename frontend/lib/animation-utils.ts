/**
 * Animation Utilities for Chat UI Components
 *
 * Based on emilkowal-animations standards:
 * - Entrances: 150-200ms ease-out
 * - Exits: 100-150ms ease-in
 * - Press feedback: scale(0.97)
 * - Stagger: 30ms per item
 * - Max duration: 300ms for UI animations
 */

/**
 * Standard animation classes following emilkowal timing
 */
export const animations = {
  // Entrance animations (ease-out)
  entrance: 'animate-fade-slide-up',
  entranceFade: 'animate-fade-in',
  entranceScale: 'animate-scale-in',
  entranceLeft: 'animate-fade-slide-left',
  entranceRight: 'animate-fade-slide-right',
  entranceDown: 'animate-fade-slide-down',

  // Exit animations (ease-in)
  exit: 'animate-fade-out',
  exitDown: 'animate-fade-slide-out-down',
  exitUp: 'animate-fade-slide-out-up',
  exitScale: 'animate-scale-out',

  // Feedback animations
  press: 'active:scale-[0.97] transition-transform duration-150',
  pressSubtle: 'active:scale-[0.98] transition-transform duration-100',
  hover: 'hover:scale-[1.02] transition-transform duration-150',
  hoverSubtle: 'hover:bg-muted/50 transition-colors duration-150',

  // Status animations
  loading: 'animate-pulse',
  spinning: 'animate-spin',
  spinSlow: 'animate-spin-slow',
  shimmer: 'animate-shimmer',
  shake: 'animate-shake',

  // Special animations
  checkDraw: 'animate-check-draw',
  cursorBlink: 'animate-cursor-blink',
  countdownPulse: 'animate-countdown-pulse',
  pulseRing: 'animate-pulse-ring',
} as const;

/**
 * Generate stagger delay class based on index
 * @param index - Zero-based index of the item
 * @param baseDelayMs - Base delay in milliseconds (default: 30ms per emilkowal)
 * @returns Tailwind style object with animation-delay
 */
export function getStaggerDelay(index: number, baseDelayMs: number = 30): { style: React.CSSProperties } {
  return {
    style: {
      animationDelay: `${index * baseDelayMs}ms`,
    },
  };
}

/**
 * Animation timing constants (emilkowal standards)
 */
export const ANIMATION_TIMING = {
  // Durations
  ENTRANCE_DURATION: 150,
  ENTRANCE_SLOW: 200,
  EXIT_DURATION: 100,
  EXIT_SLOW: 150,
  PRESS_DURATION: 150,
  MAX_UI_DURATION: 300,

  // Delays
  STAGGER_DELAY: 30,
  ROW_STAGGER_DELAY: 20,
  QUESTION_STAGGER_DELAY: 50,

  // Scale values
  PRESS_SCALE: 0.97,
  MIN_ENTER_SCALE: 0.95,
  HOVER_SCALE: 1.02,

  // Easing
  EASE_OUT: 'ease-out',
  EASE_IN: 'ease-in',
  EASE_IN_OUT: 'ease-in-out',
  SPRING: 'cubic-bezier(0.34, 1.56, 0.64, 1)',
} as const;

/**
 * Check if user prefers reduced motion
 * @returns boolean indicating preference
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined') return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/**
 * CSS transition utility for interactive elements
 */
export const transitions = {
  colors: 'transition-colors duration-150',
  transform: 'transition-transform duration-150',
  opacity: 'transition-opacity duration-150',
  all: 'transition-all duration-150',
  fast: 'transition-all duration-100',
  slow: 'transition-all duration-200',
} as const;

export type AnimationType = keyof typeof animations;
export type TransitionType = keyof typeof transitions;
