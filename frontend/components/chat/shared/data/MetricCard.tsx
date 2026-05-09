'use client';

import { memo } from 'react';
import { cn } from '@/lib/utils';
import {
  TrendUp,
  TrendDown,
  Minus,
  ArrowUp,
  ArrowDown,
} from '@phosphor-icons/react';
import { SpringCard } from '../primitives/SpringCard';
import { FadeSlide } from '../primitives/FadeSlide';
import { transitions } from '@/lib/animation-utils';

export type MetricTrend = 'up' | 'down' | 'neutral';

export interface MetricCardProps {
  /** Metric label */
  label: string;
  /** Metric value */
  value: string | number;
  /** Previous value (for comparison) */
  previousValue?: string | number;
  /** Change amount (e.g., "+12%") */
  change?: string;
  /** Trend direction */
  trend?: MetricTrend;
  /** Whether positive trend is good (default: true) */
  positiveIsGood?: boolean;
  /** Metric icon */
  icon?: React.ReactNode;
  /** Value prefix (e.g., "$") */
  prefix?: string;
  /** Value suffix (e.g., "%") */
  suffix?: string;
  /** Additional context */
  context?: string;
  /** Compact mode */
  compact?: boolean;
  /** Sparkline data points */
  sparkline?: number[];
  /** Additional CSS classes */
  className?: string;
}

/**
 * MetricCard - Display KPI metrics with trends
 *
 * Features:
 * - Trend indicators
 * - Change percentage
 * - Mini sparkline
 * - Multiple formats
 */
export const MetricCard = memo(function MetricCard({
  label,
  value,
  previousValue,
  change,
  trend = 'neutral',
  positiveIsGood = true,
  icon,
  prefix,
  suffix,
  context,
  compact = false,
  sparkline,
  className,
}: MetricCardProps) {
  // Determine if trend is positive (good)
  const isPositive = trend === 'up' ? positiveIsGood : trend === 'down' ? !positiveIsGood : true;

  // Trend icon and color
  const getTrendConfig = () => {
    if (trend === 'up') {
      return {
        icon: TrendUp,
        arrowIcon: ArrowUp,
        color: isPositive ? 'text-green-500' : 'text-red-500',
        bgColor: isPositive ? 'bg-green-500/10' : 'bg-red-500/10',
      };
    }
    if (trend === 'down') {
      return {
        icon: TrendDown,
        arrowIcon: ArrowDown,
        color: isPositive ? 'text-green-500' : 'text-red-500',
        bgColor: isPositive ? 'bg-green-500/10' : 'bg-red-500/10',
      };
    }
    return {
      icon: Minus,
      arrowIcon: Minus,
      color: 'text-muted-foreground',
      bgColor: 'bg-muted',
    };
  };

  const trendConfig = getTrendConfig();
  const TrendIcon = trendConfig.arrowIcon;

  // Render sparkline
  const renderSparkline = () => {
    if (!sparkline || sparkline.length < 2) return null;

    const min = Math.min(...sparkline);
    const max = Math.max(...sparkline);
    const range = max - min || 1;
    const height = compact ? 20 : 30;
    const width = 60;

    const points = sparkline.map((val, i) => {
      const x = (i / (sparkline.length - 1)) * width;
      const y = height - ((val - min) / range) * height;
      return `${x},${y}`;
    }).join(' ');

    return (
      <svg
        width={width}
        height={height}
        className="overflow-visible"
        viewBox={`0 0 ${width} ${height}`}
      >
        <polyline
          points={points}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={trendConfig.color}
        />
      </svg>
    );
  };

  if (compact) {
    return (
      <FadeSlide>
        <div
          className={cn(
            'flex items-center gap-3 px-3 py-2 rounded-lg border border-border bg-background',
            className
          )}
        >
          {icon && (
            <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-muted shrink-0">
              {icon}
            </div>
          )}

          <div className="flex-1 min-w-0">
            <div className="text-xs text-muted-foreground truncate">{label}</div>
            <div className="font-semibold text-sm">
              {prefix}{value}{suffix}
            </div>
          </div>

          {(change || trend !== 'neutral') && (
            <div
              className={cn(
                'flex items-center gap-0.5 text-xs font-medium',
                trendConfig.color
              )}
            >
              <TrendIcon className="h-3 w-3" weight="bold" />
              {change}
            </div>
          )}

          {sparkline && renderSparkline()}
        </div>
      </FadeSlide>
    );
  }

  return (
    <FadeSlide>
      <SpringCard variant="outline" size="sm" className={className}>
        {/* Header */}
        <div className="flex items-start justify-between mb-2">
          <div className="flex items-center gap-2">
            {icon && (
              <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-muted shrink-0">
                {icon}
              </div>
            )}
            <span className="text-sm text-muted-foreground">{label}</span>
          </div>

          {sparkline && renderSparkline()}
        </div>

        {/* Value */}
        <div className="flex items-end justify-between">
          <div>
            <div className="text-2xl font-bold tracking-tight">
              {prefix}{value}{suffix}
            </div>

            {previousValue !== undefined && (
              <div className="text-xs text-muted-foreground mt-0.5">
                Previous: {prefix}{previousValue}{suffix}
              </div>
            )}
          </div>

          {/* Change indicator */}
          {(change || trend !== 'neutral') && (
            <div
              className={cn(
                'flex items-center gap-1 px-2 py-1 rounded-md text-sm font-medium',
                trendConfig.bgColor,
                trendConfig.color
              )}
            >
              <TrendIcon className="h-4 w-4" weight="bold" />
              {change}
            </div>
          )}
        </div>

        {/* Context */}
        {context && (
          <div className="mt-2 pt-2 border-t border-border text-xs text-muted-foreground">
            {context}
          </div>
        )}
      </SpringCard>
    </FadeSlide>
  );
});

/**
 * MetricGrid - Grid layout for multiple metrics
 */
export interface MetricGridProps {
  children: React.ReactNode;
  columns?: 2 | 3 | 4;
  className?: string;
}

export const MetricGrid = memo(function MetricGrid({
  children,
  columns = 3,
  className,
}: MetricGridProps) {
  const gridCols = {
    2: 'grid-cols-1 sm:grid-cols-2',
    3: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3',
    4: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-4',
  };

  return (
    <div className={cn('grid gap-3', gridCols[columns], className)}>
      {children}
    </div>
  );
});

export type { MetricCardProps as MetricCardComponentProps };
