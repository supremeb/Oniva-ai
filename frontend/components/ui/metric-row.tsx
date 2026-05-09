'use client';

import { type ReactNode } from 'react';
import { cn } from '@/lib/utils';

export type MetricRowColumns = 2 | 3 | 4 | 5 | 6;

export interface MetricRowProps {
    /** Number of columns at the `lg` breakpoint. Collapses responsively. Default: `4`. */
    columns?: MetricRowColumns;
    /** Wrapper class override. */
    className?: string;
    children: ReactNode;
}

const columnClasses: Record<MetricRowColumns, string> = {
    2: 'grid-cols-1 sm:grid-cols-2',
    3: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3',
    4: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-4',
    5: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5',
    6: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6',
};

/**
 * Responsive grid wrapper for `<StatCard>`s. Falls back to 1–2 columns on small
 * viewports.
 */
export function MetricRow({ columns = 4, className, children }: MetricRowProps) {
    return (
        <div className={cn('grid gap-4', columnClasses[columns], className)}>
            {children}
        </div>
    );
}
