'use client';

import { useEffect, useRef, memo, useCallback } from 'react';
import { cn } from '@/lib/utils';

/**
 * GenerativeO - A plasma-inspired animated "O" indicator
 *
 * Inspired by tui-generative-art's plasma effect, this component renders
 * a ring with ASCII characters that pulse clockwise when animating.
 * When static, shows a solid dark O.
 */

export interface GenerativeOProps {
  /** Size in pixels (default: 32) */
  size?: number;
  /** Animation speed multiplier (default: 1) */
  speed?: number;
  /** Additional CSS classes */
  className?: string;
  /** Whether animation is playing - when false, shows static dark O (default: false) */
  animating?: boolean;
}

// Characters for the plasma effect - varying densities
const CHARS = ['·', '•', '●', '◉', '●', '•'];

// Colors for the wave - dark to light gradient
const WAVE_COLORS = [
  'rgba(25, 25, 25, 1)',
  'rgba(50, 50, 50, 1)',
  'rgba(85, 85, 85, 1)',
  'rgba(120, 120, 120, 1)',
  'rgba(85, 85, 85, 1)',
  'rgba(50, 50, 50, 1)',
];

// Static state - solid dark
const STATIC_COLOR = 'rgba(20, 20, 20, 1)';
const STATIC_CHAR = '●';

/**
 * Check if point is on the ring
 */
function isOnRing(
  x: number,
  y: number,
  centerX: number,
  centerY: number,
  outerRadius: number,
  innerRadius: number
): boolean {
  const dx = x - centerX;
  const dy = y - centerY;
  const dist = Math.sqrt(dx * dx + dy * dy);
  return dist >= innerRadius && dist <= outerRadius;
}

/**
 * Get angle of point relative to center (for clockwise pulse)
 */
function getAngle(x: number, y: number, centerX: number, centerY: number): number {
  return Math.atan2(y - centerY, x - centerX);
}

function GenerativeOComponent({
  size = 32,
  speed = 1,
  className,
  animating = false,
}: GenerativeOProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animationRef = useRef<number>(0);
  const timeRef = useRef(0);

  const render = useCallback((isAnimating: boolean, time: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const displaySize = size;
    const canvasSize = displaySize * dpr;

    // Set canvas size
    if (canvas.width !== canvasSize || canvas.height !== canvasSize) {
      canvas.width = canvasSize;
      canvas.height = canvasSize;
      canvas.style.width = `${displaySize}px`;
      canvas.style.height = `${displaySize}px`;
    }

    ctx.clearRect(0, 0, canvasSize, canvasSize);

    // Grid for character rendering
    const gridSize = 8;
    const cellSize = canvasSize / gridSize;
    const centerX = gridSize / 2;
    const centerY = gridSize / 2;
    const outerRadius = gridSize / 2 - 0.3;
    const innerRadius = gridSize / 4;

    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = `${cellSize * 0.85}px monospace`;

    for (let y = 0; y < gridSize; y++) {
      for (let x = 0; x < gridSize; x++) {
        if (!isOnRing(x + 0.5, y + 0.5, centerX, centerY, outerRadius, innerRadius)) {
          continue;
        }

        if (isAnimating) {
          // Get angle of this cell for clockwise pulse
          const angle = getAngle(x + 0.5, y + 0.5, centerX, centerY);

          // Clockwise wave: angle + time creates traveling wave
          // Normalize angle to 0-1 range, then add time for movement
          const normalizedAngle = (angle + Math.PI) / (Math.PI * 2);
          const wave = (normalizedAngle + time * 0.3) % 1;

          // Create pulsing effect using sine
          const pulse = (Math.sin(wave * Math.PI * 2) + 1) / 2;

          const index = Math.floor(pulse * (CHARS.length - 0.01));
          const colorIndex = Math.floor(pulse * (WAVE_COLORS.length - 0.01));

          ctx.fillStyle = WAVE_COLORS[Math.max(0, Math.min(WAVE_COLORS.length - 1, colorIndex))];
          ctx.fillText(
            CHARS[Math.max(0, Math.min(CHARS.length - 1, index))],
            (x + 0.5) * cellSize,
            (y + 0.5) * cellSize
          );
        } else {
          // Static solid O
          ctx.fillStyle = STATIC_COLOR;
          ctx.fillText(STATIC_CHAR, (x + 0.5) * cellSize, (y + 0.5) * cellSize);
        }
      }
    }
  }, [size]);

  useEffect(() => {
    render(animating, 0);

    if (animating) {
      const animate = () => {
        timeRef.current += 0.06 * speed;
        render(true, timeRef.current);
        animationRef.current = requestAnimationFrame(animate);
      };
      animationRef.current = requestAnimationFrame(animate);
    }

    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [animating, speed, render]);

  return (
    <canvas
      ref={canvasRef}
      className={cn('shrink-0', className)}
      style={{ width: size, height: size }}
      aria-label={animating ? 'Loading' : 'Logo'}
      role="img"
    />
  );
}

export const GenerativeO = memo(GenerativeOComponent);

/**
 * Inline text-based O indicator
 */
export interface GenerativeOTextProps {
  className?: string;
  animating?: boolean;
}

function GenerativeOTextComponent({ className, animating = false }: GenerativeOTextProps) {
  const chars = ['○', '◔', '◑', '◕', '●', '◕', '◑', '◔'];
  const charRef = useRef(0);
  const elementRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!animating) {
      if (elementRef.current) {
        elementRef.current.textContent = '●';
      }
      return;
    }

    const interval = setInterval(() => {
      charRef.current = (charRef.current + 1) % chars.length;
      if (elementRef.current) {
        elementRef.current.textContent = chars[charRef.current];
      }
    }, 100);

    return () => clearInterval(interval);
  }, [animating]);

  return (
    <span
      ref={elementRef}
      className={cn(
        'inline-block font-mono',
        animating ? 'text-muted-foreground' : 'text-foreground',
        className
      )}
      aria-label={animating ? 'Loading' : undefined}
      role={animating ? 'img' : undefined}
    >
      ●
    </span>
  );
}

export const GenerativeOText = memo(GenerativeOTextComponent);
