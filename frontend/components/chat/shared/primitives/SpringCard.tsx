'use client';

import { memo, forwardRef } from 'react';
import { cn } from '@/lib/utils';
import { animations, transitions } from '@/lib/animation-utils';

export interface SpringCardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Content */
  children: React.ReactNode;
  /** Whether to enable press feedback (scale to 0.97) */
  pressable?: boolean;
  /** Whether to enable hover lift effect */
  hoverable?: boolean;
  /** Whether the card is currently pressed/active */
  isPressed?: boolean;
  /** Visual variant */
  variant?: 'default' | 'outline' | 'ghost' | 'elevated';
  /** Size variant */
  size?: 'sm' | 'md' | 'lg';
  /** As a button element */
  asButton?: boolean;
  /** Click handler */
  onClick?: () => void;
  /** Disabled state */
  disabled?: boolean;
}

const variantClasses = {
  default: 'bg-card border border-border',
  outline: 'bg-transparent border border-border',
  ghost: 'bg-transparent border-transparent hover:bg-muted/50',
  elevated: 'bg-card border border-border shadow-md hover:shadow-lg',
};

const sizeClasses = {
  sm: 'p-2 rounded-lg',
  md: 'p-4 rounded-xl',
  lg: 'p-6 rounded-2xl',
};

/**
 * SpringCard - Interactive card with spring press feedback
 *
 * Press feedback following emilkowal standards:
 * - scale(0.97) on press
 * - 150ms transition duration
 * - Subtle hover lift effect (optional)
 */
export const SpringCard = memo(forwardRef<HTMLDivElement, SpringCardProps>(
  function SpringCard(
    {
      children,
      pressable = false,
      hoverable = false,
      isPressed = false,
      variant = 'default',
      size = 'md',
      asButton = false,
      onClick,
      disabled = false,
      className,
      ...props
    },
    ref
  ) {
    const isInteractive = pressable || onClick || asButton;

    const cardClasses = cn(
      variantClasses[variant],
      sizeClasses[size],
      transitions.all,
      // Press feedback
      isInteractive && !disabled && animations.press,
      // Hover effect
      hoverable && !disabled && 'hover:border-foreground/20',
      // Pressed state
      isPressed && 'scale-[0.97]',
      // Disabled state
      disabled && 'opacity-50 cursor-not-allowed',
      // Interactive cursor
      isInteractive && !disabled && 'cursor-pointer',
      className
    );

    if (asButton || onClick) {
      return (
        <div
          ref={ref}
          role="button"
          tabIndex={disabled ? -1 : 0}
          onClick={disabled ? undefined : onClick}
          onKeyDown={(e) => {
            if (!disabled && (e.key === 'Enter' || e.key === ' ')) {
              e.preventDefault();
              onClick?.();
            }
          }}
          className={cardClasses}
          aria-disabled={disabled}
          {...props}
        >
          {children}
        </div>
      );
    }

    return (
      <div ref={ref} className={cardClasses} {...props}>
        {children}
      </div>
    );
  }
));

export type { SpringCardProps as SpringCardComponentProps };
