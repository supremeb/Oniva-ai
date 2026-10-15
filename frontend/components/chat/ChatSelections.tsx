'use client';

import type { ReactNode } from 'react';
import { WebSearchPopover } from './WebSearchPopover';
import { KnowledgePopover } from './KnowledgePopover';

export interface ActiveSourcesView {
    webSearchEnabled: boolean;
    specificSites: string[];
    activeKnowledgeBaseIds: string[];
}

export interface ChatSelectionHandlers {
    onToggleWebSearch: () => void;
    onAddSite: (url: string) => void;
    onRemoveSite: (url: string) => void;
    onToggleKnowledgeBase: (kbId: string) => void;
}

/**
 * Source-selection popovers (web search, knowledge bases), passed as ReactNodes
 * into the welcome / conversation inputs.
 */
export function buildSelectionPopovers(
    activeSources: ActiveSourcesView,
    handlers: ChatSelectionHandlers,
): { webSearchPopover: ReactNode; knowledgePopover: ReactNode } {
    const webSearchPopover = (
        <WebSearchPopover
            webSearchEnabled={activeSources.webSearchEnabled}
            specificSites={activeSources.specificSites}
            onToggleWebSearch={handlers.onToggleWebSearch}
            onAddSite={handlers.onAddSite}
            onRemoveSite={handlers.onRemoveSite}
        />
    );

    const knowledgePopover = (
        <KnowledgePopover
            activeKnowledgeBaseIds={activeSources.activeKnowledgeBaseIds}
            onToggleKnowledgeBase={handlers.onToggleKnowledgeBase}
        />
    );

    return { webSearchPopover, knowledgePopover };
}
