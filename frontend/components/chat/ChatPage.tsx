'use client';

import { LegacyChat } from './LegacyChat';

interface ChatPageProps {
    projectId?: string;
}

/** Chat entry point. Picks the chat mode; only the legacy path exists until the engine branch lands (U7). */
export function ChatPage({ projectId }: ChatPageProps = {}) {
    return <LegacyChat projectId={projectId} />;
}
