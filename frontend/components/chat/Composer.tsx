'use client';

import { useEffect, useRef } from 'react';
import type { KeyboardEvent, ReactNode, RefObject } from 'react';
import { ArrowUp, Stop } from '@phosphor-icons/react';
import { GenerativeO } from '@/components/ui/GenerativeO';
import { DeepWorkProgressBar } from '@/components/deep-work/DeepWorkProgressBar';
import { AgentQuestion } from './shared/interactive/AgentQuestion';
import { DeepWorkSuggestionBanner } from './DeepWorkSuggestionBanner';
import { WelcomeInput } from './inputs/WelcomeInput';
import { ConversationInput } from './inputs/ConversationInput';
import type { Skill } from '@/lib/api/skills';
import type { useDeepWorkStore } from '@/lib/stores/deep-work';

type PendingQuestion = ReturnType<typeof useDeepWorkStore.getState>['pendingQuestion'];

/** Props shared by the welcome and conversation inputs. */
export interface ComposerInputProps {
    value: string;
    onChange: (value: string) => void;
    onSubmit: () => void;
    isLoading: boolean;
    deepWorkMode: boolean;
    onDeepWorkToggle: () => void;
    thinkingEnabled: boolean;
    onThinkingToggle: () => void;
    thinkingSupported: boolean;
    inputRef: RefObject<HTMLInputElement | null>;
    selectedSkills: Skill[];
    onRemoveSkill: (skillId: string) => void;
    sessionId: string | null;
    activeConnectorIds: string[];
    onToggleConnector: (connectorId: string) => void;
    skillPickerOpen: boolean;
    onSkillPickerOpenChange: (open: boolean) => void;
    slashQuery: string;
    onSkillSelect: (skill: Skill) => void;
    webSearchPopover: ReactNode;
    knowledgePopover: ReactNode;
}

/** Empty-state hero + welcome input. */
export function WelcomeComposer(props: ComposerInputProps) {
    return (
        <div className="flex-1 flex flex-col items-center justify-center px-6">
            <div className="flex items-center gap-4 mb-10">
                <GenerativeO size={40} speed={1} animating={false} />
                <h1 className="text-[28px] font-semibold text-neutral-900 tracking-tight">
                    How can I help?
                </h1>
            </div>
            <WelcomeInput
                value={props.value}
                onChange={props.onChange}
                onSubmit={props.onSubmit}
                isLoading={props.isLoading}
                deepWorkMode={props.deepWorkMode}
                onDeepWorkToggle={props.onDeepWorkToggle}
                thinkingEnabled={props.thinkingEnabled}
                onThinkingToggle={props.onThinkingToggle}
                thinkingSupported={props.thinkingSupported}
                inputRef={props.inputRef}
                selectedSkills={props.selectedSkills}
                onRemoveSkill={props.onRemoveSkill}
                sessionId={props.sessionId}
                activeConnectorIds={props.activeConnectorIds}
                onToggleConnector={props.onToggleConnector}
                skillPickerOpen={props.skillPickerOpen}
                onSkillPickerOpenChange={props.onSkillPickerOpenChange}
                slashQuery={props.slashQuery}
                onSkillSelect={props.onSkillSelect}
                webSearchPopover={props.webSearchPopover}
                knowledgePopover={props.knowledgePopover}
            />
        </div>
    );
}

export interface ComposerProps extends ComposerInputProps {
    isStreaming: boolean;
    onStop: () => void;
    deepWorkSuggestion: string | null;
    onAcceptDeepWork: () => void;
    onDismissDeepWork: () => void;
    isAwaitingDeepWorkInput: boolean;
    pendingQuestion: PendingQuestion;
    onStructuredAnswer: (answer: { optionIds: string[]; otherValue?: string }) => void;
    isDeepWorkExecuting: boolean;
    deepWorkActivity: ComponentDeepWorkActivity;
}

type ComponentDeepWorkActivity = React.ComponentProps<typeof ConversationInput>['deepWorkActivity'];

/** Bottom dock of an active conversation: suggestion banner, progress bar, question chips, input. */
export function Composer(props: ComposerProps) {
    const { pendingQuestion } = props;
    return (
        <>
            {props.deepWorkSuggestion && (
                <DeepWorkSuggestionBanner
                    reasoning={props.deepWorkSuggestion}
                    onAccept={props.onAcceptDeepWork}
                    onDismiss={props.onDismissDeepWork}
                />
            )}
            <DeepWorkProgressBar />
            <div className="px-6 pb-5 pt-3 ">
                {props.isAwaitingDeepWorkInput && typeof pendingQuestion === 'object' && pendingQuestion?.options ? (
                    <div className="mb-3">
                        <AgentQuestion
                            id={`dw-q-${Date.now()}`}
                            header="Agent Question"
                            question={pendingQuestion.question}
                            options={pendingQuestion.options}
                            multiSelect={pendingQuestion.multiSelect}
                            onAnswer={props.onStructuredAnswer}
                        />
                    </div>
                ) : null}
                <ConversationInput
                    value={props.value} onChange={props.onChange} onSubmit={props.onSubmit}
                    isLoading={props.isLoading} isStreaming={props.isStreaming} onStop={props.onStop}
                    deepWorkMode={props.deepWorkMode} onDeepWorkToggle={props.onDeepWorkToggle}
                    thinkingEnabled={props.thinkingEnabled} onThinkingToggle={props.onThinkingToggle} thinkingSupported={props.thinkingSupported}
                    inputRef={props.inputRef} selectedSkills={props.selectedSkills} onRemoveSkill={props.onRemoveSkill}
                    sessionId={props.sessionId} activeConnectorIds={props.activeConnectorIds} onToggleConnector={props.onToggleConnector}
                    skillPickerOpen={props.skillPickerOpen} onSkillPickerOpenChange={props.onSkillPickerOpenChange}
                    slashQuery={props.slashQuery} onSkillSelect={props.onSkillSelect}
                    webSearchPopover={props.webSearchPopover} knowledgePopover={props.knowledgePopover}
                    isDeepWorkExecuting={props.isDeepWorkExecuting}
                    isAwaitingDeepWorkInput={props.isAwaitingDeepWorkInput}
                    deepWorkActivity={props.deepWorkActivity}
                />
            </div>
        </>
    );
}

export type EngineRunState = 'idle' | 'running' | 'awaiting_approval';
export type EngineSendMode = 'submit' | 'followUp' | 'steer';

export interface EngineComposerProps {
    value: string;
    onChange: (value: string) => void;
    runState: EngineRunState;
    /** Abort sent; waiting for the run's terminal entry. */
    stopping: boolean;
    /** Conversation closed (not found / forbidden): nothing can be sent. */
    disabled?: boolean;
    onSend: (mode: EngineSendMode) => void;
    onStop: () => void;
    /** Selection popovers (web search, knowledge bases). */
    toolbar?: ReactNode;
    /** Queued-message chips rendered above the input. */
    chips?: ReactNode;
    /** Selected-skill tags rendered inside the box above the text. */
    header?: ReactNode;
    inputRef?: RefObject<HTMLTextAreaElement | null>;
    autoFocus?: boolean;
}

/**
 * Engine chat input. It never locks during a run (R14). Keys, documented here
 * and pinned by tests: Enter sends (idle: submit, running: follow-up);
 * Cmd/Ctrl+Enter steers while running (submit when idle); Shift+Enter is a
 * newline; Escape does nothing, stopping needs the Stop button.
 */
export function EngineComposer(props: EngineComposerProps) {
    const { value, runState, stopping, disabled } = props;
    const localRef = useRef<HTMLTextAreaElement>(null);
    const ref = props.inputRef ?? localRef;
    const running = runState !== 'idle';
    const empty = !value.trim();

    useEffect(() => {
        const el = ref.current;
        if (el) {
            el.style.height = 'auto';
            el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
        }
    }, [value, ref]);

    const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing) return;
        e.preventDefault();
        if (empty || disabled) return;
        if (!running) props.onSend('submit');
        else props.onSend(e.metaKey || e.ctrlKey ? 'steer' : 'followUp');
    };

    const placeholder = disabled
        ? 'This conversation is unavailable'
        : runState === 'awaiting_approval'
            ? 'Waiting for approval. You can still send a message...'
            : running
                ? 'Send a follow-up, or steer the run...'
                : 'How can I help you today';

    return (
        <div className="w-full max-w-[680px] mx-auto">
            {props.chips}
            <div className="relative rounded-xl border border-neutral-200/60 bg-white shadow-[0_1px_8px_rgba(0,0,0,0.06)] focus-within:border-neutral-300">
                {props.header}
                <textarea
                    ref={ref}
                    value={value}
                    onChange={(e) => props.onChange(e.target.value)}
                    onKeyDown={onKeyDown}
                    placeholder={placeholder}
                    aria-label="Message"
                    disabled={disabled}
                    autoFocus={props.autoFocus}
                    rows={1}
                    className="w-full min-h-[60px] max-h-[200px] resize-none rounded-xl bg-transparent pb-12 pl-5 pr-5 pt-3.5 text-[14px] text-neutral-900 placeholder:text-neutral-400 focus:outline-none"
                />
                <div className="absolute bottom-0 left-0 right-0 flex items-center justify-between px-3 pb-2.5">
                    <div className="flex items-center gap-0.5">{props.toolbar}</div>
                    <div className="flex items-center gap-2">
                        {running && (
                            <button
                                type="button"
                                onClick={props.onStop}
                                disabled={stopping || disabled}
                                aria-label={stopping ? 'Stopping' : 'Stop'}
                                className="flex h-8 items-center gap-1.5 rounded-lg border border-neutral-200 px-2.5 text-xs text-neutral-700 hover:bg-neutral-100 disabled:opacity-50"
                            >
                                <Stop className="h-3.5 w-3.5" weight="fill" />
                                {stopping ? 'Stopping...' : 'Stop'}
                            </button>
                        )}
                        {running && (
                            <button
                                type="button"
                                onClick={() => props.onSend('steer')}
                                disabled={empty || disabled}
                                title="Deliver after the current tool round (Cmd/Ctrl+Enter)"
                                className="h-8 rounded-lg border border-neutral-200 px-2.5 text-xs text-neutral-700 hover:bg-neutral-100 disabled:opacity-50"
                            >
                                Steer now
                            </button>
                        )}
                        <button
                            type="button"
                            onClick={() => props.onSend(running ? 'followUp' : 'submit')}
                            disabled={empty || disabled}
                            aria-label={running ? 'Send as follow-up' : 'Send'}
                            className="flex h-8 items-center gap-1.5 rounded-lg bg-neutral-900 px-3 text-xs text-white hover:bg-neutral-700 disabled:opacity-40"
                        >
                            {running ? 'Send as follow-up' : <ArrowUp className="h-4 w-4" weight="bold" />}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
