'use client';

import { useCallback, useEffect, ReactNode } from 'react';
import {
    ResizablePanelGroup,
    ResizablePanel,
    ResizableHandle,
} from '@/components/ui/resizable';
import {
    useAgentStudioLayout,
    PANEL_DEFAULTS,
    type AgentStudioTab,
} from '@/lib/stores/agent-studio-layout';
import { SidebarTabs } from './SidebarTabs';
import { cn } from '@/lib/utils';

interface V2LayoutProps {
    /** Left panel: document editor or chat panel */
    editorPanel: ReactNode;
    /** Resources tab content */
    resourcesContent: ReactNode;
    /** Graders tab content */
    gradersContent: ReactNode;
    /** Settings tab content */
    settingsContent: ReactNode;
    /** Background tab content */
    backgroundContent?: ReactNode;
    /** Optional className for the container */
    className?: string;
}

export function V2Layout({
    editorPanel,
    resourcesContent,
    gradersContent,
    settingsContent,
    backgroundContent,
    className,
}: V2LayoutProps) {
    const {
        chatPanelSize,
        setChatPanelSize,
        setActiveTab,
        resetPanelSizes,
    } = useAgentStudioLayout();

    // Handle panel resize
    const handleEditorResize = useCallback(
        (panelSize: { asPercentage: number; inPixels: number }) => {
            setChatPanelSize(panelSize.asPercentage);
        },
        [setChatPanelSize]
    );

    // Double-click handle to reset sizes
    const handleDoubleClick = useCallback(() => {
        resetPanelSizes();
    }, [resetPanelSizes]);

    // Keyboard shortcuts: Cmd+1/2/3 to switch sidebar tabs
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey) {
                const tabMap: Record<string, AgentStudioTab> = {
                    '1': 'resources',
                    '2': 'graders',
                    '3': 'settings',
                    '4': 'background',
                };
                const tab = tabMap[e.key];
                if (tab) {
                    e.preventDefault();
                    setActiveTab(tab);
                }
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [setActiveTab]);

    return (
        <ResizablePanelGroup
            direction="horizontal"
            className={cn('h-full', className)}
        >
            {/* Editor Panel (Left) */}
            <ResizablePanel
                defaultSize={chatPanelSize}
                minSize={PANEL_DEFAULTS.minChatSize}
                maxSize={PANEL_DEFAULTS.maxChatSize}
                onResize={handleEditorResize}
                className="bg-background"
            >
                <div className="h-full border-l-2 border-l-foreground/10">
                    {editorPanel}
                </div>
            </ResizablePanel>

            {/* Resize Handle */}
            <ResizableHandle
                onDoubleClick={handleDoubleClick}
                className={cn(
                    'w-px bg-border/80 hover:bg-primary/40 transition-colors duration-150',
                    'after:w-1 after:bg-transparent hover:after:bg-primary/10'
                )}
            />

            {/* Sidebar Panel (Right) */}
            <ResizablePanel
                defaultSize={100 - chatPanelSize}
                minSize={PANEL_DEFAULTS.minSettingsSize}
                maxSize={PANEL_DEFAULTS.maxSettingsSize}
                className="bg-muted/10"
            >
                <SidebarTabs
                    resourcesContent={resourcesContent}
                    gradersContent={gradersContent}
                    settingsContent={settingsContent}
                    backgroundContent={backgroundContent}
                />
            </ResizablePanel>
        </ResizablePanelGroup>
    );
}
