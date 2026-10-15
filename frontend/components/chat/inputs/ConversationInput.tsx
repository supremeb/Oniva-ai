'use client';

import { memo, useRef, useEffect, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { ConnectorPopover } from '../ConnectorPopover';
import { SkillPicker } from '../SkillPicker';
import { SendButton, SkillTagRow } from './shared';
import { DeepWorkToggle, ThinkingToggle } from './WelcomeInput';
import type { Skill } from '@/lib/api/skills';

interface ConversationInputProps {
    value: string;
    onChange: (value: string) => void;
    onSubmit: () => void;
    isLoading: boolean;
    isStreaming: boolean;
    onStop: () => void;
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
    webSearchPopover?: React.ReactNode;
    knowledgePopover?: React.ReactNode;
    // Deep Work mode
    isDeepWorkExecuting?: boolean;
    deepWorkActivity?: string | null;
    // Model display
    modelName?: string;
}

// Compact input for conversation mode
export const ConversationInput = memo(function ConversationInput({
    value,
    onChange,
    onSubmit,
    isLoading,
    isStreaming,
    onStop,
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
    isDeepWorkExecuting,
    deepWorkActivity,
    modelName,
}: ConversationInputProps) {
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

    const deepWorkPlaceholder = isDeepWorkExecuting
        ? 'Agent is working autonomously...'
        : undefined;
    return (
        <div className="w-full max-w-[680px] mx-auto">
            <div className={cn(
                'relative bg-white dark:bg-neutral-900 rounded-xl',
                'shadow-[0_1px_8px_rgba(0,0,0,0.06)]',
                'border border-neutral-200/60 dark:border-neutral-700/60',
                'transition-all duration-200',
                'focus-within:shadow-[0_2px_12px_rgba(0,0,0,0.1)]',
                'focus-within:border-neutral-300 dark:focus-within:border-neutral-600',
                '[&:focus-within]:ring-0'
            )}>
                {/* Skill tags */}
                {selectedSkills.length > 0 && (
                    <div className="pt-2.5">
                        <SkillTagRow skills={selectedSkills} onRemove={onRemoveSkill} />
                    </div>
                )}

                {/* Expandable textarea input */}
                <textarea
                    ref={textareaRef}
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={deepWorkPlaceholder || "How can I help you today"}
                    disabled={isDeepWorkExecuting}
                    rows={1}
                    className={cn(
                        'w-full pl-5 pr-5 pt-3.5 pb-12 bg-transparent resize-none',
                        'min-h-[60px] max-h-[200px]',
                        'text-[14px] text-neutral-900 dark:text-neutral-100 placeholder:text-neutral-400',
                        'focus:outline-none focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0',
                        'rounded-xl',
                        isDeepWorkExecuting && 'opacity-50 cursor-not-allowed'
                    )}
                />

                {/* Bottom toolbar */}
                <div className="absolute bottom-0 left-0 right-0 px-3 pb-2.5 flex items-center justify-between">
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
                        <ThinkingToggle active={thinkingEnabled} onToggle={onThinkingToggle} supported={thinkingSupported} compact />
                        <DeepWorkToggle active={deepWorkMode} onToggle={onDeepWorkToggle} compact />
                    </div>

                    {/* Right side - model badge + send button */}
                    <div className="flex items-center gap-2.5">
                        {modelName && (
                            <span className="text-[11px] text-muted-foreground bg-muted/50 px-2 py-0.5 rounded-md truncate max-w-[120px]">
                                {modelName}
                            </span>
                        )}
                        <SendButton
                            onClick={isStreaming ? onStop : onSubmit}
                            disabled={!value.trim() && !isStreaming}
                            isStreaming={isStreaming}
                            onStop={onStop}
                            size="compact"
                        />
                    </div>
                </div>
            </div>
        </div>
    );
});
