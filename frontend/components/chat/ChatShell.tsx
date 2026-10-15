'use client';

import type { ReactNode } from 'react';
import { ChatErrorBoundary } from './shared';
import {
    ShareNetwork,
    DotsThreeOutline,
    PencilSimple,
    Trash,
    FolderSimple,
    ArrowLeft,
    SidebarSimple,
} from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import { TooltipProvider } from '@/components/ui/tooltip';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useChatUIStore } from '@/lib/stores/chat-ui';
import { getAgentIcon } from '@/lib/agent-icons';
import type { AgentDisplayInfo } from '@/hooks/useAgentInfo';

export interface ChatShellProps {
    projectId?: string;
    /** Project name for project-scoped chats (undefined while loading). */
    projectName?: string;
    agentInfo: AgentDisplayInfo | null;
    hasMessages: boolean;
    onBackToProject: () => void;
    /** Omit both to hide the overflow menu (engine chat has no rename/delete route yet). */
    onRename?: () => void;
    onDelete?: () => void;
    /** Empty-state hero + welcome input (shown when there are no messages). */
    welcome: ReactNode;
    /** Scrollable transcript (shown when there are messages). */
    messageArea: ReactNode;
    /** Bottom dock (banner, progress, input); shown when there are messages. */
    composer: ReactNode;
    /** Right-hand file/source panel content (shown when the panel is open). */
    rightPanel: ReactNode;
    /** Modal overlays rendered after the main column. */
    overlay?: ReactNode;
    /** Extra header content before the actions (engine chat: conversation cost). */
    headerExtra?: ReactNode;
}

/** Layout and header for the chat page: header actions, optional right panel, content slots. */
export function ChatShell({
    projectId,
    projectName,
    agentInfo,
    hasMessages,
    onBackToProject,
    onRename,
    onDelete,
    welcome,
    messageArea,
    composer,
    rightPanel,
    overlay,
    headerExtra,
}: ChatShellProps) {
    // Right panel UI state (separate store per Kieran P1)
    const { rightPanelOpen, toggleRightPanel } = useChatUIStore();

    return (
        <ChatErrorBoundary>
            <TooltipProvider delayDuration={300}>
            <div className="flex flex-col h-full bg-white">
                {/* Header — always rendered; agent indicator centered, actions on right */}
                <div className="flex items-center h-12 px-5">
                    {/* Left: back to project link (when in project) */}
                    <div className="flex-1">
                        {projectId && (
                            <a
                                href={`/chat/projects/${projectId}`}
                                onClick={(e) => {
                                    e.preventDefault();
                                    onBackToProject();
                                }}
                                className="flex items-center gap-1 text-sm text-neutral-500 hover:text-neutral-800 transition-colors"
                            >
                                <ArrowLeft className="h-3.5 w-3.5" />
                                <span>Back to project</span>
                            </a>
                        )}
                    </div>

                    {/* Centered agent/project indicator */}
                    {projectId && projectName !== undefined ? (
                        <div className="flex items-center gap-1.5">
                            <FolderSimple className="h-4 w-4 text-amber-600" weight="fill" />
                            <span className="text-sm font-medium text-neutral-700">{projectName}</span>
                        </div>
                    ) : agentInfo ? (() => {
                        const AgentIcon = getAgentIcon(agentInfo.icon);
                        return (
                            <div className="flex items-center gap-1.5">
                                <AgentIcon className={cn('h-4 w-4', agentInfo.color)} weight="fill" />
                                <span className="text-sm font-medium text-neutral-700">{agentInfo.name}</span>
                            </div>
                        );
                    })() : null}

                    {/* Right actions */}
                    <div className="flex-1 flex justify-end gap-2">
                        {hasMessages && (
                            <>
                                {headerExtra}
                                <button className={cn(
                                    'flex items-center gap-2 px-3 py-1.5 rounded-lg',
                                    'text-sm text-neutral-600 hover:text-neutral-900',
                                    'hover:bg-neutral-200/60 transition-colors'
                                )}>
                                    <ShareNetwork className="h-4 w-4" weight="regular" />
                                    <span>Share Chat</span>
                                </button>
                                <button
                                    onClick={toggleRightPanel}
                                    className={cn(
                                        'h-8 w-8 flex items-center justify-center rounded-lg transition-colors',
                                        rightPanelOpen
                                            ? 'text-neutral-900 bg-neutral-200/60'
                                            : 'text-neutral-500 hover:text-neutral-700 hover:bg-neutral-200/60',
                                    )}
                                    title={rightPanelOpen ? 'Close panel' : 'Open panel'}
                                >
                                    <SidebarSimple className="h-5 w-5" weight={rightPanelOpen ? 'fill' : 'regular'} />
                                </button>
                                {(onRename || onDelete) && (<DropdownMenu>
                                    <DropdownMenuTrigger asChild>
                                        <button className={cn(
                                            'h-8 w-8 flex items-center justify-center rounded-lg',
                                            'text-neutral-500 hover:text-neutral-700',
                                            'hover:bg-neutral-200/60 transition-colors'
                                        )}>
                                            <DotsThreeOutline className="h-5 w-5" weight="fill" />
                                        </button>
                                    </DropdownMenuTrigger>
                                    <DropdownMenuContent align="end" className="w-40">
                                        <DropdownMenuItem onClick={onRename} disabled={!onRename}>
                                            <PencilSimple className="h-3.5 w-3.5 mr-2" />
                                            Rename
                                        </DropdownMenuItem>
                                        <DropdownMenuSeparator />
                                        <DropdownMenuItem
                                            onClick={onDelete}
                                            disabled={!onDelete}
                                            className="text-destructive focus:text-destructive"
                                        >
                                            <Trash className="h-3.5 w-3.5 mr-2" />
                                            Delete
                                        </DropdownMenuItem>
                                    </DropdownMenuContent>
                                </DropdownMenu>)}
                            </>
                        )}
                    </div>
                </div>

                {/* Main content area — optionally split with right panel */}
                {rightPanelOpen && hasMessages ? (
                    <div className="flex flex-1 min-h-0">
                        {/* Chat content */}
                        <div className="flex-1 flex flex-col min-w-0">
                            {messageArea}
                            {composer}
                        </div>
                        {/* Right panel - fixed width */}
                        <div className="w-[340px] min-w-[280px] border-l border-neutral-200/60">
                            {rightPanel}
                        </div>
                    </div>
                ) : (
                    <>
                        {/* Content area */}
                        {!hasMessages ? welcome : messageArea}

                        {hasMessages && composer}
                    </>
                )}
            </div>

            {overlay}
            </TooltipProvider>
        </ChatErrorBoundary>
    );
}
