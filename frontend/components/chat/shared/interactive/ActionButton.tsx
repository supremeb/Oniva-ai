'use client';

import { memo, forwardRef, type ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';
import { CircleNotch } from '@phosphor-icons/react';
import { animations, transitions } from '@/lib/animation-utils';

export interface ActionButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Button variant */
  variant?: 'primary' | 'secondary' | 'outline' | 'ghost' | 'destructive';
  /** Button size */
  size?: 'sm' | 'md' | 'lg';
  /** Loading state */
  loading?: boolean;
  /** Icon to display before text */
  icon?: React.ReactNode;
  /** Icon to display after text */
  iconAfter?: React.ReactNode;
  /** Full width button */
  fullWidth?: boolean;
}

const variantStyles = {
  primary: 'bg-primary text-primary-foreground hover:bg-primary/90',
  secondary: 'bg-secondary text-secondary-foreground hover:bg-secondary/80',
  outline: 'border border-input bg-background hover:bg-accent hover:text-accent-foreground',
  ghost: 'hover:bg-accent hover:text-accent-foreground',
  destructive: 'bg-destructive text-destructive-foreground hover:bg-destructive/90',
};

const sizeStyles = {
  sm: 'h-8 px-3 text-xs gap-1.5',
  md: 'h-9 px-4 text-sm gap-2',
  lg: 'h-10 px-6 text-base gap-2',
};

/**
 * ActionButton - Interactive button with press feedback
 *
 * Features:
 * - Spring press animation (scale 0.97)
 * - Loading state with spinner
 * - Icon support (before/after)
 * - Multiple variants and sizes
 */
export const ActionButton = memo(
  forwardRef<HTMLButtonElement, ActionButtonProps>(function ActionButton(
    {
      variant = 'primary',
      size = 'md',
      loading = false,
      icon,
      iconAfter,
      fullWidth = false,
      disabled,
      className,
      children,
      ...props
    },
    ref
  ) {
    const isDisabled = disabled || loading;

    return (
      <button
        ref={ref}
        disabled={isDisabled}
        className={cn(
          'inline-flex items-center justify-center rounded-md font-medium',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
          'disabled:pointer-events-none disabled:opacity-50',
          variantStyles[variant],
          sizeStyles[size],
          fullWidth && 'w-full',
          !isDisabled && animations.press,
          transitions.colors,
          className
        )}
        {...props}
      >
        {loading ? (
          <CircleNotch className="h-4 w-4 animate-spin" weight="bold" />
        ) : icon ? (
          <span className="shrink-0">{icon}</span>
        ) : null}

        {children && <span>{children}</span>}

        {iconAfter && !loading && (
          <span className="shrink-0">{iconAfter}</span>
        )}
      </button>
    );
  })
);

export type { ActionButtonProps as ActionButtonComponentProps };
