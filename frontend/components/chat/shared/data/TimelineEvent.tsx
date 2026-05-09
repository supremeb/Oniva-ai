'use client';

import { memo } from 'react';
import { cn } from '@/lib/utils';
import {
  Circle,
  Check,
  Warning,
  Clock,
  ArrowRight,
  Lightning,
} from '@phosphor-icons/react';
import { FadeSlide } from '../primitives/FadeSlide';
import { transitions } from '@/lib/animation-utils';

export type TimelineEventStatus = 'pending' | 'active' | 'complete' | 'error' | 'skipped';

export interface TimelineEventProps {
  /** Event title */
  title: string;
  /** Event description */
  description?: string;
  /** Event status */
  status?: TimelineEventStatus;
  /** Timestamp */
  timestamp?: string;
  /** Duration */
  duration?: string;
  /** Custom icon */
  icon?: React.ReactNode;
  /** Whether this is the last event (no connecting line) */
  isLast?: boolean;
  /** Whether this is the first event */
  isFirst?: boolean;
  /** Compact mode */
  compact?: boolean;
  /** Show connector line */
  showConnector?: boolean;
  /** Children (for nested events) */
  children?: React.ReactNode;
  /** Additional CSS classes */
  className?: string;
}

const statusConfig = {
  pending: {
    icon: Circle,
    color: 'text-muted-foreground',
    bgColor: 'bg-muted',
    lineColor: 'bg-muted-foreground/30',
  },
  active: {
    icon: Lightning,
    color: 'text-primary',
    bgColor: 'bg-primary/10',
    lineColor: 'bg-primary/30',
  },
  complete: {
    icon: Check,
    color: 'text-green-500',
    bgColor: 'bg-green-500/10',
    lineColor: 'bg-green-500/50',
  },
  error: {
    icon: Warning,
    color: 'text-red-500',
    bgColor: 'bg-red-500/10',
    lineColor: 'bg-red-500/30',
  },
  skipped: {
    icon: ArrowRight,
    color: 'text-neutral-400',
    bgColor: 'bg-neutral-500/10',
    lineColor: 'bg-neutral-500/30',
  },
};

/**
 * TimelineEvent - Single event in a timeline
 *
 * Features:
 * - Multiple status states
 * - Custom icons
 * - Connecting lines
 * - Nested events
 */
export const TimelineEvent = memo(function TimelineEvent({
  title,
  description,
  status = 'pending',
  timestamp,
  duration,
  icon,
  isLast = false,
  isFirst = false,
  compact = false,
  showConnector = true,
  children,
  className,
}: TimelineEventProps) {
  const config = statusConfig[status] || statusConfig.pending;
  const StatusIcon = config.icon;

  return (
    <FadeSlide className={className}>
      <div className="flex gap-3">
        {/* Timeline column */}
        <div className="flex flex-col items-center">
          {/* Icon */}
          <div
            className={cn(
              'flex items-center justify-center rounded-full shrink-0',
              config.bgColor,
              compact ? 'w-6 h-6' : 'w-8 h-8',
              status === 'active' && 'ring-2 ring-primary/30'
            )}
          >
            {icon || (
              <StatusIcon
                className={cn(
                  config.color,
                  compact ? 'h-3 w-3' : 'h-4 w-4',
                  status === 'active' && 'animate-pulse'
                )}
                weight={status === 'complete' ? 'bold' : 'fill'}
              />
            )}
          </div>

          {/* Connecting line */}
          {showConnector && !isLast && (
            <div
              className={cn(
                'w-0.5 flex-1 min-h-8 my-1',
                config.lineColor
              )}
            />
          )}
        </div>

        {/* Content */}
        <div className={cn('flex-1 min-w-0', isLast ? 'pb-0' : 'pb-6')}>
          {/* Header */}
          <div className="flex items-start justify-between gap-2">
            <div>
              <h4
                className={cn(
                  'font-medium',
                  compact ? 'text-sm' : 'text-sm',
                  status === 'pending' && 'text-muted-foreground',
                  status === 'active' && 'text-foreground',
                  status === 'complete' && 'text-foreground',
                  status === 'error' && 'text-red-500',
                  status === 'skipped' && 'text-neutral-400 line-through'
                )}
              >
                {title}
              </h4>
              {description && (
                <p className="text-xs text-muted-foreground mt-0.5">
                  {description}
                </p>
              )}
            </div>

            {/* Metadata */}
            <div className="flex items-center gap-2 text-xs text-muted-foreground shrink-0">
              {duration && (
                <span className="flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  {duration}
                </span>
              )}
              {timestamp && <span>{timestamp}</span>}
            </div>
          </div>

          {/* Nested events */}
          {children && (
            <div className="mt-3 pl-2 border-l-2 border-muted">
              {children}
            </div>
          )}
        </div>
      </div>
    </FadeSlide>
  );
});

/**
 * Timeline - Container for timeline events
 */
export interface TimelineProps {
  /** Timeline events */
  children: React.ReactNode;
  /** Compact mode */
  compact?: boolean;
  /** Additional CSS classes */
  className?: string;
}

export const Timeline = memo(function Timeline({
  children,
  compact = false,
  className,
}: TimelineProps) {
  return (
    <div className={cn('space-y-0', compact && 'space-y-0', className)}>
      {children}
    </div>
  );
});

export type { TimelineEventProps as TimelineEventComponentProps };
