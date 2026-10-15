'use client';

import { useState } from 'react';
import { useEngineChatMode } from '@/lib/hooks/useFeature';
import { EngineChat } from './EngineChat';
import { LegacyChat } from './LegacyChat';

interface ChatPageProps {
    projectId?: string;
    /** Explicit conversation to open (sidebar); overrides `?c=`. */
    conversationId?: string | null;
}

/** Chat entry point. The tenant flag picks the mode once per mount (KTD1); `?c=` rejoins an engine conversation. */
export function ChatPage({ projectId, conversationId: explicitId }: ChatPageProps = {}) {
    const mode = useEngineChatMode();
    // Read once: the URL is written by EngineChat itself. (useSearchParams would force a Suspense boundary on the page.)
    const [urlId] = useState<string | null>(() =>
        typeof window === 'undefined' ? null : new URLSearchParams(window.location.search).get('c')
    );
    const conversationId = explicitId !== undefined ? explicitId : urlId;
    if (mode === 'engine') {
        return <EngineChat projectId={projectId} initialConversationId={conversationId} />;
    }
    return <LegacyChat projectId={projectId} />;
}
