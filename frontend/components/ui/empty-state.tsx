'use client';

import { memo, type ReactNode } from 'react';
import { Sparkle } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';

export type EmptyStateSize = 'sm' | 'md' | 'lg';
export type EmptyStateIconTreatment = 'plain' | 'gradient';

export interface EmptyStateProps {
    /** Icon node — typically a Phosphor icon element. */
    icon?: ReactNode;
    /** Primary heading. */
    title: string;
    /** Supporting description. */
    description?: string;
    /** Action slot — typically a Button or link. */
    action?: ReactNode;
    /** Optional pills rendered between description and suggestions (feature tags, badges). */
    pills?: ReactNode;
    /** Optional clickable suggestion strings. */
    suggestions?: string[];
    /** Callback when a suggestion is clicked. */
    onSuggestionClick?: (suggestion: string) => void;
    /** Heading shown above the suggestion list. */
    suggestionsHeading?: string;
    /** Sizing scale. Default: `md`. */
    size?: EmptyStateSize;
    /** Visual treatment for the icon block. Default: `plain`. */
    iconTreatment?: EmptyStateIconTreatment;
    /** Custom gradient classes when `iconTreatment="gradient"`. */
    gradientClassName?: string;
    /** Optional class to tint the icon. */
    iconColorClassName?: string;
    /** Extra wrapper classes. */
    className?: string;
}

const sizeClasses: Record<
    EmptyStateSize,
    {
        wrapper: string;
        iconBlock: string;
        iconBlockRadius: string;
        iconSize: string;
        title: string;
        description: string;
        gap: string;
    }
> = {
    sm: {
        wrapper: 'min-h-[160px] p-4',
        iconBlock: 'w-12 h-12',
        iconBlockRadius: 'rounded-xl',
        iconSize: 'h-6 w-6',
        title: 'text-sm font-medium',
        description: 'text-xs text-muted-foreground max-w-[220px]',
        gap: 'gap-2',
    },
    md: {
        wrapper: 'min-h-[280px] p-6',
        iconBlock: 'w-16 h-16',
        iconBlockRadius: 'rounded-2xl',
        iconSize: 'h-8 w-8',
        title: 'text-base font-semibold',
        description: 'text-sm text-muted-foreground max-w-md',
        gap: 'gap-3',
    },
    lg: {
        wrapper: 'min-h-[400px] p-8',
        iconBlock: 'w-20 h-20',
        iconBlockRadius: 'rounded-2xl',
        iconSize: 'h-10 w-10',
        title: 'text-heading-3',
        description: 'text-body-muted max-w-md',
        gap: 'gap-3',
    },
};

const SuggestionButton = memo(function SuggestionButton({
    suggestion,
    onClick,
}: {
    suggestion: string;
    onClick: () => void;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            className={cn(
                'group text-left px-4 py-2.5 rounded-xl',
                'bg-muted/30 hover:bg-muted/60',
                'border border-border/40 hover:border-border/60',
                'text-sm text-muted-foreground hover:text-foreground',
                'transition-[colors,border-color] duration-200 ease-[cubic-bezier(0.2,0,0,1)]',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
            )}
        >
            <span className="flex items-center gap-2">
                <Sparkle
                    className="h-3.5 w-3.5 text-primary/60 group-hover:text-primary transition-colors duration-200"
                    weight="duotone"
                />
                {suggestion}
            </span>
        </button>
    );
});

/**
 * Generic empty-state primitive. Use for "no data yet" surfaces across the app.
 * Domain wrappers (e.g. `ChatEmptyState`) compose this with their own presets.
 */
export const EmptyState = memo(function EmptyState({
    icon,
    title,
    description,
    action,
    pills,
    suggestions,
    onSuggestionClick,
    suggestionsHeading = 'Try one of these prompts:',
    size = 'md',
    iconTreatment = 'plain',
    gradientClassName,
    iconColorClassName,
    className,
}: EmptyStateProps) {
    const classes = sizeClasses[size];
    const showSuggestions =
        suggestions && suggestions.length > 0 && onSuggestionClick;

    return (
        <div
            className={cn(
                'flex-1 flex flex-col items-center justify-center text-center',
                classes.wrapper,
                className,
            )}
        >
            {icon && (
                <div
                    className={cn(
                        'flex items-center justify-center mb-5',
                        classes.iconBlock,
                        classes.iconBlockRadius,
                        iconTreatment === 'gradient' && [
                            'bg-gradient-to-br',
                            gradientClassName ?? 'from-primary/10 to-primary/5',
                            'ring-1 ring-black/5 dark:ring-white/10',
                            'shadow-lg shadow-primary/5',
                        ],
                        iconTreatment === 'plain' && 'text-muted-foreground/60',
                        iconColorClassName,
                    )}
                >
                    <span className={cn(classes.iconSize, 'flex items-center justify-center')}>
                        {icon}
                    </span>
                </div>
            )}

            <h3 className={cn(classes.title, 'text-balance')}>{title}</h3>

            {description && (
                <p className={cn(classes.description, 'mt-2 text-pretty')}>
                    {description}
                </p>
            )}

            {pills && <div className="mt-4 flex flex-wrap justify-center gap-2">{pills}</div>}

            {action && <div className="mt-5">{action}</div>}

            {showSuggestions && (
                <div className="w-full max-w-md mt-6 space-y-2">
                    <p className="text-xs text-muted-foreground/80 mb-3">
                        {suggestionsHeading}
                    </p>
                    <div className="flex flex-col gap-2">
                        {suggestions.map((suggestion, index) => (
                            <SuggestionButton
                                key={index}
                                suggestion={suggestion}
                                onClick={() => onSuggestionClick(suggestion)}
                            />
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
});
