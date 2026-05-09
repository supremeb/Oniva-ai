'use client';

import { memo } from 'react';
import { cn } from '@/lib/utils';
import { Check, Circle, CircleNotch, Warning, X } from '@phosphor-icons/react';
import { FadeSlide } from '../primitives/FadeSlide';
import { transitions } from '@/lib/animation-utils';

export type StepStatus = 'pending' | 'active' | 'complete' | 'error' | 'skipped';

export interface Step {
  id: string;
  label: string;
  description?: string;
  status: StepStatus;
}

export interface StepIndicatorProps {
  /** Steps array */
  steps: Step[];
  /** Display orientation */
  orientation?: 'horizontal' | 'vertical';
  /** Show step numbers */
  showNumbers?: boolean;
  /** Compact mode */
  compact?: boolean;
  /** Show descriptions */
  showDescriptions?: boolean;
  /** Additional CSS classes */
  className?: string;
}

const statusConfig = {
  pending: {
    icon: Circle,
    color: 'text-muted-foreground/50',
    bgColor: 'bg-muted',
    ringColor: '',
    weight: 'regular' as const,
    animate: false,
  },
  active: {
    icon: CircleNotch,
    color: 'text-primary',
    bgColor: 'bg-primary/10',
    ringColor: 'ring-2 ring-primary/30',
    weight: 'regular' as const,
    animate: true,
  },
  complete: {
    icon: Check,
    color: 'text-green-500',
    bgColor: 'bg-green-500/10',
    ringColor: '',
    weight: 'bold' as const,
    animate: false,
  },
  error: {
    icon: X,
    color: 'text-red-500',
    bgColor: 'bg-red-500/10',
    ringColor: '',
    weight: 'bold' as const,
    animate: false,
  },
  skipped: {
    icon: Circle,
    color: 'text-muted-foreground/30',
    bgColor: 'bg-muted/50',
    ringColor: '',
    weight: 'regular' as const,
    animate: false,
  },
};

/**
 * StepIndicator - Multi-step progress indicator
 *
 * Features:
 * - Horizontal/vertical layouts
 * - Step numbers
 * - Status icons
 * - Descriptions
 */
export const StepIndicator = memo(function StepIndicator({
  steps,
  orientation = 'horizontal',
  showNumbers = true,
  compact = false,
  showDescriptions = true,
  className,
}: StepIndicatorProps) {
  const isHorizontal = orientation === 'horizontal';

  return (
    <FadeSlide>
      <div
        className={cn(
          isHorizontal ? 'flex items-start' : 'flex flex-col',
          className
        )}
      >
        {steps.map((step, index) => {
          const config = statusConfig[step.status];
          const StepIcon = config.icon;
          const isLast = index === steps.length - 1;

          return (
            <div
              key={step.id}
              className={cn(
                'flex',
                isHorizontal ? 'flex-1 items-start' : 'items-start gap-3'
              )}
            >
              {/* Step circle and connector */}
              <div
                className={cn(
                  'flex',
                  isHorizontal
                    ? 'flex-col items-center flex-1'
                    : 'flex-col items-center'
                )}
              >
                {/* Circle */}
                <div
                  className={cn(
                    'flex items-center justify-center rounded-full shrink-0',
                    config.bgColor,
                    config.ringColor,
                    compact ? 'w-6 h-6' : 'w-8 h-8'
                  )}
                >
                  {showNumbers && step.status === 'pending' ? (
                    <span
                      className={cn(
                        'font-medium',
                        compact ? 'text-xs' : 'text-sm',
                        config.color
                      )}
                    >
                      {index + 1}
                    </span>
                  ) : (
                    <StepIcon
                      className={cn(
                        config.color,
                        compact ? 'h-3 w-3' : 'h-4 w-4',
                        config.animate && 'animate-spin'
                      )}
                      weight={config.weight}
                    />
                  )}
                </div>

                {/* Horizontal connector */}
                {isHorizontal && !isLast && (
                  <div
                    className={cn(
                      'h-0.5 mt-4 mx-2',
                      'flex-1 min-w-8',
                      step.status === 'complete'
                        ? 'bg-green-500/50'
                        : 'bg-muted-foreground/20'
                    )}
                  />
                )}

                {/* Vertical connector */}
                {!isHorizontal && !isLast && (
                  <div
                    className={cn(
                      'w-0.5 my-1 min-h-6 flex-1',
                      step.status === 'complete'
                        ? 'bg-green-500/50'
                        : 'bg-muted-foreground/20'
                    )}
                  />
                )}
              </div>

              {/* Label and description */}
              <div
                className={cn(
                  isHorizontal
                    ? 'text-center mt-2 px-2'
                    : cn('flex-1 min-w-0', isLast ? 'pb-0' : 'pb-6')
                )}
              >
                <div
                  className={cn(
                    'font-medium',
                    compact ? 'text-xs' : 'text-sm',
                    step.status === 'pending' && 'text-muted-foreground/70',
                    step.status === 'active' && 'text-foreground',
                    step.status === 'complete' && 'text-foreground',
                    step.status === 'error' && 'text-red-500',
                    step.status === 'skipped' &&
                      'text-muted-foreground/50 line-through'
                  )}
                >
                  {step.label}
                </div>

                {showDescriptions && step.description && (
                  <p
                    className={cn(
                      'text-muted-foreground mt-0.5',
                      compact ? 'text-[10px]' : 'text-xs',
                      isHorizontal && 'max-w-24'
                    )}
                  >
                    {step.description}
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </FadeSlide>
  );
});

/**
 * ProgressSteps - Simpler numbered progress
 */
export interface ProgressStepsProps {
  total: number;
  current: number;
  labels?: string[];
  className?: string;
}

export const ProgressSteps = memo(function ProgressSteps({
  total,
  current,
  labels,
  className,
}: ProgressStepsProps) {
  return (
    <div className={cn('flex items-center gap-2', className)}>
      {Array.from({ length: total }, (_, i) => (
        <div key={i} className="flex items-center">
          <div
            className={cn(
              'flex items-center justify-center w-6 h-6 rounded-full text-xs font-medium',
              transitions.colors,
              i < current
                ? 'bg-green-500/20 text-green-500'
                : i === current
                ? 'bg-primary/20 text-primary'
                : 'bg-muted text-muted-foreground'
            )}
          >
            {i < current ? (
              <Check className="h-3 w-3" weight="bold" />
            ) : (
              i + 1
            )}
          </div>

          {labels && labels[i] && (
            <span
              className={cn(
                'ml-1.5 text-xs',
                i <= current ? 'text-foreground' : 'text-muted-foreground'
              )}
            >
              {labels[i]}
            </span>
          )}

          {i < total - 1 && (
            <div
              className={cn(
                'w-4 h-0.5 mx-1',
                i < current ? 'bg-green-500/50' : 'bg-muted'
              )}
            />
          )}
        </div>
      ))}
    </div>
  );
});

export type { StepIndicatorProps as StepIndicatorComponentProps };
