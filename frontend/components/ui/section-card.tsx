'use client';

import { type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { Card } from '@/components/ui/card';

export interface SectionCardProps {
    /** Section heading. */
    title?: ReactNode;
    /** Supporting description below the title. */
    description?: ReactNode;
    /** Right-aligned slot for inline actions. */
    actions?: ReactNode;
    /** Section body. */
    children: ReactNode;
    /** Removes the divider between header and body. */
    flushHeader?: boolean;
    /** Custom padding on the body region. Default: `p-5`. */
    bodyClassName?: string;
    /** Wrapper class override. */
    className?: string;
}

/**
 * Inset section container intended for nesting inside default/hero cards or
 * directly on the page background. Uses `Card variant="inset"` for concentric
 * radius pairing with surrounding cards.
 */
export function SectionCard({
    title,
    description,
    actions,
    children,
    flushHeader,
    bodyClassName,
    className,
}: SectionCardProps) {
    const hasHeader = Boolean(title || actions || description);

    return (
        <Card variant="inset" className={cn('overflow-hidden', className)}>
            {hasHeader && (
                <div
                    className={cn(
                        'flex items-start justify-between gap-3 px-5 py-4',
                        !flushHeader && 'border-b border-border/60',
                    )}
                >
                    <div className="min-w-0 flex-1">
                        {title && (
                            <h2 className="text-sm font-semibold tracking-tight text-balance">
                                {title}
                            </h2>
                        )}
                        {description && (
                            <p className="mt-1 text-xs text-muted-foreground text-pretty">
                                {description}
                            </p>
                        )}
                    </div>
                    {actions && (
                        <div className="flex items-center gap-2 shrink-0">{actions}</div>
                    )}
                </div>
            )}

            <div className={cn(bodyClassName ?? 'p-5')}>{children}</div>
        </Card>
    );
}
