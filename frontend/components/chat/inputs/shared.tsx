'use client';

import { memo } from 'react';
import { ArrowUp, Clock, X } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type { Skill } from '@/lib/api/skills';

/**
 * Shared props passed from ChatInterface to both input variants.
 * Ephemeral UI state (deepWorkMode, thinkingEnabled, selectedSkills, activeSources)
 * is read directly from Zustand stores inside each component.
 */
export interface InputBarProps {
    value: string;
    onChange: (value: string) => void;
    onSubmit: () => void;
    isLoading: boolean;
    inputRef: React.RefObject<HTMLInputElement | null>;
    sessionId: string | null;
}

// Send button component with Radix tooltip
export const SendButton = memo(function SendButton({
    onClick,
    disabled,
    isStreaming,
    onStop,
    size = 'default',
}: {
    onClick: () => void;
    disabled: boolean;
    isStreaming?: boolean;
    onStop?: () => void;
    size?: 'default' | 'compact';
}) {
    const sizeClasses = size === 'compact' ? 'h-8 w-8' : 'h-9 w-9';
    const iconSize = size === 'compact' ? 'h-4 w-4' : 'h-4 w-4';

    if (isStreaming) {
        return (
            <Tooltip>
                <TooltipTrigger asChild>
                    <button
                        onClick={onStop}
                        className={cn(
                            sizeClasses,
                            'rounded-full flex items-center justify-center transition-all duration-200',
                            'bg-neutral-800 hover:bg-neutral-700 text-white',
                            'shadow-sm'
                        )}
                    >
                        <div className="h-2.5 w-2.5 rounded-sm bg-current" />
                    </button>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={8}>
                    <p>Stop generating</p>
                </TooltipContent>
            </Tooltip>
        );
    }

    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <button
                    onClick={onClick}
                    disabled={disabled}
                    className={cn(
                        sizeClasses,
                        'rounded-full flex items-center justify-center transition-all duration-200',
                        disabled
                            ? 'bg-neutral-200 text-neutral-400 cursor-not-allowed'
                            : 'bg-neutral-800 hover:bg-neutral-700 text-white shadow-sm hover:shadow'
                    )}
                >
                    <ArrowUp className={iconSize} weight="bold" />
                </button>
            </TooltipTrigger>
            <TooltipContent side="top" sideOffset={8}>
                <p>Send message</p>
            </TooltipContent>
        </Tooltip>
    );
});

// Input action button with Radix tooltip
export const InputAction = memo(function InputAction({
    icon: Icon,
    onClick,
    title,
    disabled,
}: {
    icon: typeof Clock;
    onClick?: () => void;
    title?: string;
    disabled?: boolean;
}) {
    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <button
                    onClick={onClick}
                    disabled={disabled}
                    className={cn(
                        'h-8 w-8 flex items-center justify-center rounded-lg',
                        'transition-colors duration-150',
                        disabled
                            ? 'text-neutral-300 cursor-not-allowed'
                            : 'text-neutral-400 hover:text-neutral-600 hover:bg-neutral-100'
                    )}
                >
                    <Icon className="h-[18px] w-[18px]" weight="regular" />
                </button>
            </TooltipTrigger>
            {title && (
                <TooltipContent side="top" sideOffset={8}>
                    <p>{title}</p>
                </TooltipContent>
            )}
        </Tooltip>
    );
});

// Skill tag row — renders selected skills as removable tags
export const SkillTagRow = memo(function SkillTagRow({
    skills,
    onRemove,
}: {
    skills: Skill[];
    onRemove: (skillId: string) => void;
}) {
    return (
        <div className="flex flex-wrap gap-1.5 px-4 min-h-0">
            {skills.map((skill) => (
                <span
                    key={skill.id}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-neutral-100 text-neutral-800 border border-neutral-200 text-xs font-medium"
                >
                    {skill.name}
                    <button
                        onClick={() => onRemove(skill.id)}
                        className="hover:text-neutral-900"
                        aria-label={`Remove ${skill.name}`}
                    >
                        <X size={12} />
                    </button>
                </span>
            ))}
        </div>
    );
});
