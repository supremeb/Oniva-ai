'use client';

import { type ReactNode } from 'react';
import { cn } from '@/lib/utils';

export type PageHeaderSize = 'compact' | 'default' | 'hero';

export interface PageHeaderProps {
    /** Small label above the title (e.g., section name, breadcrumb tail). */
    eyebrow?: ReactNode;
    /** Primary heading. Wrapped in `<h1>`. */
    title: ReactNode;
    /** Supporting description below the title. */
    description?: ReactNode;
    /** Right-aligned slot for action buttons. Buttons inside should be ≥40×40 hit area. */
    actions?: ReactNode;
    /** Optional breadcrumbs row rendered above the eyebrow. */
    breadcrumbs?: ReactNode;
    /** Size variant. Default: `default`. */
    size?: PageHeaderSize;
    /** Wrapper class override. */
    className?: string;
    /** Slot rendered below the description (filters, tabs, search). */
    footer?: ReactNode;
}

const sizeClasses: Record<
    PageHeaderSize,
    { wrapper: string; title: string; description: string; actionsGap: string }
> = {
    compact: {
        wrapper: 'pb-4',
        title: 'text-lg font-semibold tracking-tight',
        description: 'text-sm text-muted-foreground',
        actionsGap: 'gap-2',
    },
    default: {
        wrapper: 'pb-6',
        title: 'text-2xl font-semibold tracking-tight',
        description: 'text-sm text-muted-foreground',
        actionsGap: 'gap-2',
    },
    hero: {
        wrapper: 'pb-8',
        title: 'text-3xl font-semibold tracking-tight',
        description: 'text-base text-muted-foreground',
        actionsGap: 'gap-3',
    },
};

export function PageHeader({
    eyebrow,
    title,
    description,
    actions,
    breadcrumbs,
    size = 'default',
    className,
    footer,
}: PageHeaderProps) {
    const classes = sizeClasses[size];

    return (
        <header className={cn(classes.wrapper, className)}>
            {breadcrumbs && (
                <div className="mb-3 text-xs text-muted-foreground">{breadcrumbs}</div>
            )}

            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0 flex-1">
                    {eyebrow && (
                        <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground/80">
                            {eyebrow}
                        </div>
                    )}
                    <h1 className={cn(classes.title, 'text-balance')}>{title}</h1>
                    {description && (
                        <p className={cn(classes.description, 'mt-2 text-pretty max-w-2xl')}>
                            {description}
                        </p>
                    )}
                </div>

                {actions && (
                    <div
                        className={cn(
                            'flex flex-wrap items-center shrink-0',
                            classes.actionsGap,
                        )}
                    >
                        {actions}
                    </div>
                )}
            </div>

            {footer && <div className="mt-4">{footer}</div>}
        </header>
    );
}
