'use client';

import type { ReactNode, RefObject } from 'react';
import { ScrollArea } from '@/components/ui/scroll-area';
import { CompactionMarker } from './CompactionMarker';
import { ChatMessageList, ChatLoadingIndicator } from './shared';
import type { ChatMessageListProps } from './shared/ChatMessage';
import type { ArtifactModalData } from './shared/data/FilePreview';
import type { EngineMessage, PendingEntry } from '@/lib/engine';

export interface EngineMessageListProps {
    scrollRef: RefObject<HTMLDivElement | null>;
    viewportRef: RefObject<HTMLDivElement | null>;
    messages: EngineMessage[];
    /** Optimistic user entries not yet in the server view (already filtered for display). */
    pending: PendingEntry[];
    running: boolean;
    /** A compaction task is running (transient "Summarizing earlier messages"). */
    compacting?: boolean;
    onRetry: (requestId: string) => void;
    onOpenModal: (data: ArtifactModalData) => void;
    approvalHandlers?: ChatMessageListProps['approvalHandlers'];
    renderMessageFooter?: ChatMessageListProps['renderMessageFooter'];
    /** Rendered after the transcript (the Deep Work intake/run card). */
    footer?: ReactNode;
}

export const pendingText = (p: PendingEntry): string =>
    p.text ?? (typeof p.content === 'string' ? p.content : '');

/**
 * Transcript for engine chat. The live container is an `aria-live` log so a screen
 * reader hears new messages; `aria-busy` while a run streams defers announcements
 * until the text settles instead of reading every 100 ms diff.
 */
export function EngineMessageList({ scrollRef, viewportRef, messages, pending, running, compacting = false, onRetry, onOpenModal, approvalHandlers, renderMessageFooter, footer }: EngineMessageListProps) {
    const streaming = messages.find((m) => m.isStreaming);
    const waiting = running && !streaming && !compacting;
    return (
        <ScrollArea className="flex-1" ref={scrollRef} viewportRef={viewportRef}>
            <div
                role="log"
                aria-live="polite"
                aria-relevant="additions text"
                aria-busy={running}
                aria-label="Conversation"
                data-testid="engine-message-log"
                className="px-6 py-6 max-w-[720px] mx-auto w-full"
            >
                <ChatMessageList
                    messages={messages}
                    renderMarkdown
                    showAvatars
                    streamingMessageId={streaming?.id}
                    streamingRichParts={streaming?.richParts ?? []}
                    onOpenModal={onOpenModal}
                    approvalHandlers={approvalHandlers}
                    renderMessageFooter={renderMessageFooter}
                />
                {pending.map((p) => (
                    <div key={p.requestId} className="mt-6 flex justify-end" data-testid="pending-message" data-status={p.status}>
                        <div className="max-w-[85%] rounded-2xl bg-neutral-100 px-4 py-2.5 text-[14px] text-neutral-900 whitespace-pre-wrap">
                            {pendingText(p)}
                            <div className="mt-1 text-xs text-neutral-400">
                                {p.status === 'failed' ? (
                                    <>
                                        Not sent.{' '}
                                        <button type="button" className="underline" onClick={() => onRetry(p.requestId)}>
                                            Retry
                                        </button>
                                    </>
                                ) : (
                                    'Sending...'
                                )}
                            </div>
                        </div>
                    </div>
                ))}
                {footer}
                {compacting && <CompactionMarker state="running" />}
                {waiting && <ChatLoadingIndicator message="Thinking..." variant="generative" className="mt-6" />}
            </div>
        </ScrollArea>
    );
}
