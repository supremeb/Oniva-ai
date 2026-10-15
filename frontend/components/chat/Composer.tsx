'use client';

import type { ReactNode, RefObject } from 'react';
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
