'use client';

import { memo, useRef, useEffect, useCallback } from 'react';
import { MagnifyingGlass, Brain } from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import { ENGINE_DEEP_WORK_FLAG, useFeature } from '@/lib/hooks/useFeature';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { ConnectorPopover } from '../ConnectorPopover';
import { SkillPicker } from '../SkillPicker';
import { SendButton, InputAction, SkillTagRow } from './shared';
import type { Skill } from '@/lib/api/skills';

interface WelcomeInputProps {
    value: string;
    onChange: (value: string) => void;
    onSubmit: () => void;
    isLoading: boolean;
    deepWorkMode: boolean;
    onDeepWorkToggle: () => void;
    thinkingEnabled: boolean;
    onThinkingToggle: () => void;
    thinkingSupported: boolean;
    inputRef: React.RefObject<HTMLTextAreaElement | HTMLInputElement | null>;
    selectedSkills: Skill[];
    onRemoveSkill: (skillId: string) => void;
    sessionId: string | null;
    activeConnectorIds: string[];
    onToggleConnector: (connectorId: string) => void;
    skillPickerOpen: boolean;
    onSkillPickerOpenChange: (open: boolean) => void;
    slashQuery: string;
    onSkillSelect: (skill: Skill) => void;
    // Source control props
    webSearchEnabled?: boolean;
    onWebSearchToggle?: () => void;
    specificSitesCount?: number;
    onOpenWebPopover?: () => void;
    activeKnowledgeBaseCount?: number;
    onOpenKnowledgePopover?: () => void;
    webSearchPopover?: React.ReactNode;
    knowledgePopover?: React.ReactNode;
}

// Deep Work toggle button
const DeepWorkToggle = memo(function DeepWorkToggle({
    active,
    onToggle,
    compact = false,
}: {
    active: boolean;
    onToggle: () => void;
    compact?: boolean;
}) {
    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <button
                    onClick={onToggle}
                    className={cn(
                        'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg transition-all duration-200 text-xs font-medium',
                        active
                            ? 'bg-neutral-100 text-neutral-900 border border-neutral-300 shadow-sm'
                            : 'text-neutral-400 hover:text-neutral-600 hover:bg-neutral-100'
                    )}
                >
                    <MagnifyingGlass className={cn('h-4 w-4', active && 'text-neutral-900')} weight={active ? 'fill' : 'regular'} />
                    {!compact && <span>{active ? 'Deep Work' : 'Deep Work'}</span>}
                </button>
            </TooltipTrigger>
            <TooltipContent side="top" sideOffset={8}>
                <p>{active ? 'Deep Work mode ON — agent will plan before acting' : 'Enable Deep Work'}</p>
            </TooltipContent>
        </Tooltip>
    );
});

// Thinking mode toggle button
const ThinkingToggle = memo(function ThinkingToggle({
    active,
    onToggle,
    supported,
    compact = false,
}: {
    active: boolean;
    onToggle: () => void;
    supported: boolean;
    compact?: boolean;
}) {
    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <button
                    onClick={onToggle}
                    className={cn(
                        'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg transition-all duration-200 text-xs font-medium',
                        active
                            ? 'bg-purple-50 text-purple-700 border border-purple-200 shadow-sm'
                            : 'text-neutral-400 hover:text-neutral-600 hover:bg-neutral-100'
                    )}
                >
                    <Brain className={cn('h-4 w-4', active && 'text-purple-600')} weight={active ? 'fill' : 'regular'} />
                    {!compact && <span>{active ? 'Thinking' : 'Think'}</span>}
                </button>
            </TooltipTrigger>
            <TooltipContent side="top" sideOffset={8}>
                <p>{active ? 'Extended thinking ON — model will show its reasoning' : 'Enable extended thinking'}</p>
            </TooltipContent>
        </Tooltip>
    );
});

// Welcome screen input - large, centered, inviting
export const WelcomeInput = memo(function WelcomeInput({
    value,
    onChange,
    onSubmit,
    isLoading,
    deepWorkMode,
    onDeepWorkToggle,
    thinkingEnabled,
    onThinkingToggle,
    thinkingSupported,
    inputRef,
    selectedSkills,
    onRemoveSkill,
    sessionId,
    activeConnectorIds,
    onToggleConnector,
    skillPickerOpen,
    onSkillPickerOpenChange,
    slashQuery,
    onSkillSelect,
    webSearchPopover,
    knowledgePopover,
}: WelcomeInputProps) {
    // The Deep Work toggle only exists where Deep Work runs on the engine (per-tenant flag).
    const deepWorkAvailable = useFeature(ENGINE_DEEP_WORK_FLAG);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    // Auto-resize textarea
    useEffect(() => {
        const el = textareaRef.current;
        if (el) {
            el.style.height = 'auto';
            el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
        }
    }, [value]);

    // Forward ref for focus management
    useEffect(() => {
        if (inputRef && 'current' in inputRef) {
            (inputRef as React.MutableRefObject<HTMLTextAreaElement | HTMLInputElement | null>).current = textareaRef.current;
        }
    }, [inputRef]);

    const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            onSubmit();
        }
    }, [onSubmit]);

    return (
        <div className="w-full max-w-[680px] mx-auto">
            <div className={cn(
                'relative bg-white dark:bg-neutral-900 rounded-2xl',
                'shadow-[0_2px_12px_rgba(0,0,0,0.08)]',
                'border border-neutral-200/60 dark:border-neutral-700/60',
                'transition-all duration-200',
                'focus-within:shadow-[0_4px_20px_rgba(0,0,0,0.12)]',
                'focus-within:border-neutral-300 dark:focus-within:border-neutral-600',
                '[&:focus-within]:ring-0'
            )}>
                {/* Skill tags */}
                {selectedSkills.length > 0 && (
                    <div className="pt-3">
                        <SkillTagRow skills={selectedSkills} onRemove={onRemoveSkill} />
                    </div>
                )}

                {/* Expandable textarea input */}
                <textarea
                    ref={textareaRef}
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="How can I help you today"
                    rows={1}
                    className={cn(
                        'w-full px-5 pt-4 pb-14 bg-transparent resize-none',
                        'min-h-[60px] max-h-[200px]',
                        'text-[15px] text-neutral-900 dark:text-neutral-100 placeholder:text-neutral-400',
                        'focus:outline-none focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0',
                        'rounded-2xl'
                    )}
                    autoFocus
                />

                {/* Bottom toolbar */}
                <div className="absolute bottom-0 left-0 right-0 px-3 pb-3 flex items-center justify-between">
                    {/* Left actions */}
                    <div className="flex items-center gap-0.5">
                        {sessionId && (
                            <ConnectorPopover
                                sessionId={sessionId}
                                activeConnectorIds={activeConnectorIds}
                                onToggle={onToggleConnector}
                            />
                        )}
                        {webSearchPopover}
                        {knowledgePopover}
                        <SkillPicker
                            open={skillPickerOpen}
                            onOpenChange={onSkillPickerOpenChange}
                            initialQuery={slashQuery}
                            selectedSkillIds={selectedSkills.map(s => s.id)}
                            onSkillSelect={onSkillSelect}
                        />
                        <div className="ml-1 flex items-center gap-1">
                            <ThinkingToggle active={thinkingEnabled} onToggle={onThinkingToggle} supported={thinkingSupported} />
                            {deepWorkAvailable && <DeepWorkToggle active={deepWorkMode} onToggle={onDeepWorkToggle} />}
                        </div>
                    </div>

                    {/* Right side - send button */}
                    <div className="flex items-center gap-3">
                        <SendButton
                            onClick={onSubmit}
                            disabled={!value.trim() || isLoading}
                            isStreaming={false}
                        />
                    </div>
                </div>
            </div>
        </div>
    );
});

export { DeepWorkToggle, ThinkingToggle };
