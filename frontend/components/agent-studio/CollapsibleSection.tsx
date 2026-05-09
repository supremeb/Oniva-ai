'use client';

import { ReactNode } from 'react';
import { CaretDown } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import {
    Collapsible,
    CollapsibleContent,
    CollapsibleTrigger,
} from '@/components/ui/collapsible';

interface CollapsibleSectionProps {
    /** Unique identifier for the section (used for state persistence) */
    id: string;
    /** Section title */
    title: string;
    /** Optional icon to display before the title */
    icon?: ReactNode;
    /** Optional badge/count to display after the title */
    badge?: ReactNode;
    /** Whether the section is open */
    open: boolean;
    /** Callback when open state changes */
    onOpenChange: (open: boolean) => void;
    /** Section content */
    children: ReactNode;
    /** Additional className for the container */
    className?: string;
    /** Additional className for the content area */
    contentClassName?: string;
    /** Whether to show a subtle background on the header */
    headerBg?: boolean;
}

export function CollapsibleSection({
    id,
    title,
    icon,
    badge,
    open,
    onOpenChange,
    children,
    className,
    contentClassName,
    headerBg = false,
}: CollapsibleSectionProps) {
    return (
        <Collapsible
            open={open}
            onOpenChange={onOpenChange}
            className={cn('rounded-lg border bg-card', className)}
        >
            <CollapsibleTrigger asChild>
                <button
                    type="button"
                    className={cn(
                        'flex w-full items-center justify-between px-3.5 py-2.5 text-left transition-colors',
                        'hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
                        'rounded-t-lg',
                        !open && 'rounded-b-lg',
                        headerBg && 'bg-muted/30'
                    )}
                >
                    <div className="flex items-center gap-2">
                        {icon && (
                            <span className="text-primary shrink-0">
                                {icon}
                            </span>
                        )}
                        <span className="text-[13px] font-medium leading-none">{title}</span>
                        {badge}
                    </div>
                    <CaretDown
                        className={cn(
                            'h-3.5 w-3.5 text-muted-foreground transition-transform duration-200 shrink-0',
                            open && 'rotate-180'
                        )}
                        weight="bold"
                    />
                </button>
            </CollapsibleTrigger>
            <CollapsibleContent>
                <div className={cn('px-3.5 pb-3.5 pt-1', contentClassName)}>
                    {children}
                </div>
            </CollapsibleContent>
        </Collapsible>
    );
}

// Simplified version without external state management - for standalone use
interface SimpleCollapsibleSectionProps {
    title: string;
    icon?: ReactNode;
    badge?: ReactNode;
    defaultOpen?: boolean;
    children: ReactNode;
    className?: string;
    contentClassName?: string;
}

export function SimpleCollapsibleSection({
    title,
    icon,
    badge,
    defaultOpen = true,
    children,
    className,
    contentClassName,
}: SimpleCollapsibleSectionProps) {
    return (
        <Collapsible
            defaultOpen={defaultOpen}
            className={cn('rounded-lg border bg-card', className)}
        >
            <CollapsibleTrigger asChild>
                <button
                    type="button"
                    className={cn(
                        'flex w-full items-center justify-between px-3.5 py-2.5 text-left transition-colors',
                        'hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
                        'rounded-lg group data-[state=open]:rounded-b-none'
                    )}
                >
                    <div className="flex items-center gap-2">
                        {icon && (
                            <span className="text-primary shrink-0">
                                {icon}
                            </span>
                        )}
                        <span className="text-[13px] font-medium leading-none">{title}</span>
                        {badge}
                    </div>
                    <CaretDown
                        className="h-3.5 w-3.5 text-muted-foreground transition-transform duration-200 shrink-0 group-data-[state=open]:rotate-180"
                        weight="bold"
                    />
                </button>
            </CollapsibleTrigger>
            <CollapsibleContent>
                <div className={cn('px-3.5 pb-3.5 pt-1', contentClassName)}>
                    {children}
                </div>
            </CollapsibleContent>
        </Collapsible>
    );
}
