'use client';

import { memo } from 'react';
import { cn } from '@/lib/utils';
import { Check, Circle, CircleNotch, Warning } from '@phosphor-icons/react';
import { StaggerContainer } from '../primitives/StaggerContainer';

export interface ToolCallStep {
  id: string;
  label: string;
  status: 'pending' | 'running' | 'complete' | 'error';
  detail?: string;
}

export interface ToolCallProgressProps {
  /** Steps in the tool execution */
  steps: ToolCallStep[];
  /** Show step details */
  showDetails?: boolean;
  /** Compact display mode */
  compact?: boolean;
  /** Additional CSS classes */
  className?: string;
}

const stepStatusConfig = {
  pending: {
    icon: Circle,
    color: 'text-muted-foreground/50',
    lineColor: 'bg-muted-foreground/20',
    weight: 'regular' as const,
    animate: false,
  },
  running: {
    icon: CircleNotch,
    color: 'text-primary',
    lineColor: 'bg-primary/30',
    weight: 'regular' as const,
    animate: true,
  },
  complete: {
    icon: Check,
    color: 'text-green-500',
    lineColor: 'bg-green-500/50',
    weight: 'bold' as const,
    animate: false,
  },
  error: {
    icon: Warning,
    color: 'text-destructive',
    lineColor: 'bg-destructive/30',
    weight: 'regular' as const,
    animate: false,
  },
};

/**
 * ToolCallProgress - Step-by-step execution progress display
 *
 * Shows:
 * - Vertical step list with status icons
 * - Connecting lines between steps
 * - Current step highlight
 * - Staggered entrance animation
 */
export const ToolCallProgress = memo(function ToolCallProgress({
  steps,
  showDetails = false,
  compact = false,
  className,
}: ToolCallProgressProps) {
  if (steps.length === 0) {
    return null;
  }

  return (
    <div className={cn('relative', className)}>
      <StaggerContainer
        staggerDelay={50}
        animation="fade-slide-up"
        className="relative"
      >
        {steps.map((step, index) => {
          const config = stepStatusConfig[step.status];
          const StepIcon = config.icon;
          const isLast = index === steps.length - 1;

          return (
            <div key={step.id} className="flex gap-3">
              {/* Icon and connecting line */}
              <div className="flex flex-col items-center">
                {/* Icon */}
                <div
                  className={cn(
                    'flex items-center justify-center rounded-full shrink-0',
                    compact ? 'w-5 h-5' : 'w-6 h-6',
                    step.status === 'running' && 'bg-primary/10',
                    step.status === 'complete' && 'bg-green-500/10',
                    step.status === 'error' && 'bg-destructive/10',
                    step.status === 'pending' && 'bg-muted'
                  )}
                >
                  <StepIcon
                    className={cn(
                      config.color,
                      compact ? 'h-3 w-3' : 'h-3.5 w-3.5',
                      config.animate && 'animate-spin'
                    )}
                    weight={config.weight}
                  />
                </div>

                {/* Connecting line */}
                {!isLast && (
                  <div
                    className={cn(
                      'w-0.5 flex-1 min-h-4 my-1',
                      config.lineColor
                    )}
                  />
                )}
              </div>

              {/* Step content */}
              <div className={cn('flex-1 pb-4', isLast && 'pb-0')}>
                <div
                  className={cn(
                    'font-medium',
                    compact ? 'text-xs' : 'text-sm',
                    step.status === 'pending' && 'text-muted-foreground/70',
                    step.status === 'running' && 'text-foreground',
                    step.status === 'complete' && 'text-foreground',
                    step.status === 'error' && 'text-destructive'
                  )}
                >
                  {step.label}
                </div>

                {/* Step detail */}
                {showDetails && step.detail && (
                  <div className="text-xs text-muted-foreground mt-0.5">
                    {step.detail}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </StaggerContainer>
    </div>
  );
});

/**
 * ToolCallProgressInline - Horizontal step indicator
 */
export interface ToolCallProgressInlineProps {
  steps: ToolCallStep[];
  className?: string;
}

export const ToolCallProgressInline = memo(function ToolCallProgressInline({
  steps,
  className,
}: ToolCallProgressInlineProps) {
  const completeCount = steps.filter((s) => s.status === 'complete').length;
  const currentStep = steps.find((s) => s.status === 'running');

  return (
    <div className={cn('flex items-center gap-2', className)}>
      <span className="text-xs text-muted-foreground">
        {completeCount}/{steps.length} steps
      </span>

      {currentStep && (
        <>
          <span className="text-muted-foreground/30">|</span>
          <span className="text-xs text-foreground flex items-center gap-1">
            <CircleNotch className="h-3 w-3 animate-spin text-primary" />
            {currentStep.label}
          </span>
        </>
      )}
    </div>
  );
});

export type { ToolCallProgressProps as ToolCallProgressComponentProps };
