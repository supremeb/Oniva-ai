'use client';

import { memo, useMemo } from 'react';
import { cn } from '@/lib/utils';
import { prefersReducedMotion } from '@/lib/animation-utils';

export interface StreamingRevealProps {
  /** Text content to reveal */
  content: string;
  /** Whether currently streaming */
  isStreaming?: boolean;
  /** Reveal mode: 'word' reveals word by word, 'character' character by character */
  mode?: 'word' | 'character' | 'line';
  /** Base delay between reveals in milliseconds */
  revealDelay?: number;
  /** Additional CSS classes */
  className?: string;
  /** Class for each revealed unit (word/character/line) */
  unitClassName?: string;
}

/**
 * StreamingReveal - Animated text reveal for streaming content
 *
 * Reveals text progressively with fade-in animations.
 * Optimized for streaming responses where content appears incrementally.
 */
export const StreamingReveal = memo(function StreamingReveal({
  content,
  isStreaming = false,
  mode = 'word',
  revealDelay = 20,
  className,
  unitClassName,
}: StreamingRevealProps) {
  const reducedMotion = prefersReducedMotion();

  const units = useMemo(() => {
    switch (mode) {
      case 'character':
        return content.split('');
      case 'line':
        return content.split('\n');
      case 'word':
      default:
        // Split by word but preserve whitespace
        return content.split(/(\s+)/).filter(Boolean);
    }
  }, [content, mode]);

  // If reduced motion or not streaming, render plain text
  if (reducedMotion || !isStreaming) {
    return <span className={className}>{content}</span>;
  }

  return (
    <span className={cn('inline', className)}>
      {units.map((unit, index) => {
        const isWhitespace = /^\s+$/.test(unit);

        if (isWhitespace) {
          // Render whitespace directly without animation
          return <span key={index}>{unit}</span>;
        }

        return (
          <span
            key={index}
            className={cn(
              'inline-block animate-fade-in animate-fill-both',
              unitClassName
            )}
            style={{
              animationDelay: `${index * revealDelay}ms`,
              animationDuration: '100ms',
            }}
          >
            {unit}
          </span>
        );
      })}
    </span>
  );
});

/**
 * StreamingLine - Single line reveal for code/terminal output
 */
export interface StreamingLineProps {
  /** Line content */
  content: string;
  /** Line index for stagger delay */
  index?: number;
  /** Delay per line */
  lineDelay?: number;
  /** Additional classes */
  className?: string;
}

export const StreamingLine = memo(function StreamingLine({
  content,
  index = 0,
  lineDelay = 50,
  className,
}: StreamingLineProps) {
  const reducedMotion = prefersReducedMotion();

  if (reducedMotion) {
    return <div className={className}>{content}</div>;
  }

  return (
    <div
      className={cn('animate-fade-slide-up animate-fill-both', className)}
      style={{
        animationDelay: `${index * lineDelay}ms`,
      }}
    >
      {content}
    </div>
  );
});

export type { StreamingRevealProps as StreamingRevealComponentProps };
