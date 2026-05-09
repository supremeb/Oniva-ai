'use client';

import { memo } from 'react';
import { cn } from '@/lib/utils';
import {
  Check,
  Warning,
  Info,
  X,
  CircleNotch,
  Clock,
  Pause,
  Lightning,
} from '@phosphor-icons/react';
import { transitions } from '@/lib/animation-utils';

export type StatusType =
  | 'success'
  | 'error'
  | 'warning'
  | 'info'
  | 'pending'
  | 'running'
  | 'paused'
  | 'cancelled'
  | 'neutral';

export type StatusSize = 'xs' | 'sm' | 'md' | 'lg';

export interface StatusBadgeProps {
  /** Status type */
  status: StatusType;
  /** Badge label */
  label?: string;
  /** Badge size */
  size?: StatusSize;
  /** Show icon */
  showIcon?: boolean;
  /** Custom icon */
  icon?: React.ReactNode;
  /** Pulsing animation for active states */
  pulse?: boolean;
  /** Outlined variant */
  outlined?: boolean;
  /** Additional CSS classes */
  className?: string;
}

const statusConfig = {
  success: {
    icon: Check,
    label: 'Success',
    color: 'text-green-600 dark:text-green-400',
    bgColor: 'bg-green-500/10',
    borderColor: 'border-green-500/30',
  },
  error: {
    icon: X,
    label: 'Error',
    color: 'text-red-600 dark:text-red-400',
    bgColor: 'bg-red-500/10',
    borderColor: 'border-red-500/30',
  },
  warning: {
    icon: Warning,
    label: 'Warning',
    color: 'text-amber-600 dark:text-amber-400',
    bgColor: 'bg-amber-500/10',
    borderColor: 'border-amber-500/30',
  },
  info: {
    icon: Info,
    label: 'Info',
    color: 'text-blue-600 dark:text-blue-400',
    bgColor: 'bg-blue-500/10',
    borderColor: 'border-blue-500/30',
  },
  pending: {
    icon: Clock,
    label: 'Pending',
    color: 'text-muted-foreground',
    bgColor: 'bg-muted',
    borderColor: 'border-muted-foreground/30',
  },
  running: {
    icon: CircleNotch,
    label: 'Running',
    color: 'text-primary',
    bgColor: 'bg-primary/10',
    borderColor: 'border-primary/30',
  },
  paused: {
    icon: Pause,
    label: 'Paused',
    color: 'text-orange-600 dark:text-orange-400',
    bgColor: 'bg-orange-500/10',
    borderColor: 'border-orange-500/30',
  },
  cancelled: {
    icon: X,
    label: 'Cancelled',
    color: 'text-neutral-500',
    bgColor: 'bg-neutral-500/10',
    borderColor: 'border-neutral-500/30',
  },
  neutral: {
    icon: Lightning,
    label: 'Active',
    color: 'text-foreground',
    bgColor: 'bg-muted',
    borderColor: 'border-border',
  },
};

const sizeConfig = {
  xs: {
    padding: 'px-1.5 py-0.5',
    text: 'text-[10px]',
    iconSize: 'h-2.5 w-2.5',
    gap: 'gap-1',
  },
  sm: {
    padding: 'px-2 py-0.5',
    text: 'text-xs',
    iconSize: 'h-3 w-3',
    gap: 'gap-1',
  },
  md: {
    padding: 'px-2.5 py-1',
    text: 'text-sm',
    iconSize: 'h-3.5 w-3.5',
    gap: 'gap-1.5',
  },
  lg: {
    padding: 'px-3 py-1.5',
    text: 'text-base',
    iconSize: 'h-4 w-4',
    gap: 'gap-2',
  },
};

/**
 * StatusBadge - Display status indicators
 *
 * Features:
 * - Multiple status types
 * - Size variants
 * - Animated states
 * - Outlined variant
 */
export const StatusBadge = memo(function StatusBadge({
  status,
  label,
  size = 'sm',
  showIcon = true,
  icon,
  pulse = false,
  outlined = false,
  className,
}: StatusBadgeProps) {
  const config = statusConfig[status];
  const sizeStyles = sizeConfig[size];
  const StatusIcon = config.icon;

  const displayLabel = label ?? config.label;
  const shouldPulse = pulse || status === 'running';

  return (
    <span
      className={cn(
        'inline-flex items-center font-medium rounded-full',
        sizeStyles.padding,
        sizeStyles.text,
        sizeStyles.gap,
        config.color,
        outlined
          ? cn('border', config.borderColor, 'bg-transparent')
          : config.bgColor,
        shouldPulse && 'animate-pulse',
        transitions.colors,
        className
      )}
    >
      {showIcon && (
        icon || (
          <StatusIcon
            className={cn(
              sizeStyles.iconSize,
              status === 'running' && 'animate-spin'
            )}
            weight={status === 'success' || status === 'error' ? 'bold' : 'fill'}
          />
        )
      )}
      {displayLabel && <span>{displayLabel}</span>}
    </span>
  );
});

export type { StatusBadgeProps as StatusBadgeComponentProps };
