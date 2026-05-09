'use client';

import { type ReactNode } from 'react';
import Link from 'next/link';
import { ArrowRight } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import { Card } from '@/components/ui/card';
import { DeltaIndicator } from '@/components/ui/delta-indicator';

export interface StatCardProps {
    /** Small muted label. */
    label: ReactNode;
    /** Primary value. Numbers automatically render with `tabular-nums`. */
    value: ReactNode;
    /** Optional decimal delta (`0.05` = +5%) — uses `DeltaIndicator`. */
    delta?: number | null;
    /** Optional icon node — typically a Phosphor icon at `h-5 w-5`. */
    icon?: ReactNode;
    /** Optional supporting hint below the value. */
    hint?: ReactNode;
    /** If provided, the card becomes a link and gains interactive feedback. */
    href?: string;
    /** Click handler — also activates interactive feedback. */
    onClick?: () => void;
    /** Wrapper class override. */
    className?: string;
}

/**
 * Composable stat tile. Composes `<Card variant="interactive">` when navigable,
 * `<Card>` otherwise. Numbers use `tabular-nums` to prevent layout shift.
 */
export function StatCard({
    label,
    value,
    delta,
    icon,
    hint,
    href,
    onClick,
    className,
}: StatCardProps) {
    const isInteractive = Boolean(href || onClick);

    const inner = (
        <Card
            variant={isInteractive ? 'interactive' : 'default'}
            className={cn('flex flex-col gap-3 p-5 h-full', className)}
        >
            <div className="flex items-start justify-between gap-3">
                <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground/80">
                    {label}
                </span>
                {icon && (
                    <span className="shrink-0 text-muted-foreground/70">
                        {icon}
                    </span>
                )}
            </div>

            <div className="flex items-baseline gap-2">
                <span className="text-2xl font-semibold tabular-nums tracking-tight">
                    {value}
                </span>
                {typeof delta === 'number' && (
                    <DeltaIndicator delta={delta} size="sm" />
                )}
            </div>

            {(hint || isInteractive) && (
                <div className="flex items-center justify-between gap-2 mt-auto">
                    {hint ? (
                        <span className="text-xs text-muted-foreground">{hint}</span>
                    ) : (
                        <span />
                    )}
                    {isInteractive && (
                        <ArrowRight
                            className="h-3.5 w-3.5 text-muted-foreground/60"
                            weight="bold"
                        />
                    )}
                </div>
            )}
        </Card>
    );

    if (href) {
        return (
            <Link
                href={href}
                className="block h-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-xl"
            >
                {inner}
            </Link>
        );
    }

    if (onClick) {
        return (
            <button
                type="button"
                onClick={onClick}
                className="block h-full text-left w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-xl"
            >
                {inner}
            </button>
        );
    }

    return inner;
}
