'use client';

import { Terminal, ChartLine, Code, CaretUp, CaretDown, X, CornersOut, CornersIn } from '@phosphor-icons/react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useBottomPanel, type BottomPanelTab } from '@/lib/stores/bottom-panel';
import { cn } from '@/lib/utils';

const TAB_CONFIG: Record<BottomPanelTab, { icon: typeof Terminal; label: string }> = {
    logs: { icon: Terminal, label: 'Logs' },
    monitoring: { icon: ChartLine, label: 'Monitoring' },
    api: { icon: Code, label: 'API' },
};

interface BottomPanelHeaderProps {
    className?: string;
}

export function BottomPanelHeader({ className }: BottomPanelHeaderProps) {
    const { activeTab, isCollapsed, isMaximized, setActiveTab, togglePanel, toggleMaximize, setCollapsed } = useBottomPanel();

    return (
        <div
            className={cn(
                'flex items-center justify-between px-6 py-5 bg-white dark:bg-zinc-900',
                // Top border for consistency with side panels
                'border-t border-slate-200 dark:border-zinc-800',
                // Only show bottom border when expanded
                !isCollapsed && 'border-b',
                className
            )}
            role="tablist"
            aria-label="Activity panel tabs"
        >
            {/* Tab Buttons */}
            <div className="flex items-center gap-0.5">
                {(Object.entries(TAB_CONFIG) as [BottomPanelTab, typeof TAB_CONFIG.logs][]).map(
                    ([tabId, { icon: Icon, label }]) => (
                        <button
                            key={tabId}
                            role="tab"
                            aria-selected={!isCollapsed && activeTab === tabId}
                            aria-controls={`panel-${tabId}`}
                            tabIndex={activeTab === tabId ? 0 : -1}
                            onClick={() => {
                                setActiveTab(tabId);
                                // Clicking a tab when collapsed should expand the panel
                                if (isCollapsed) {
                                    togglePanel();
                                }
                            }}
                            className={cn(
                                'h-8 px-3 rounded-md text-xs font-medium flex items-center gap-1.5',
                                'transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400',
                                // Only show active styling when panel is expanded
                                !isCollapsed && activeTab === tabId
                                    ? 'bg-zinc-800 text-zinc-100'
                                    : 'text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50'
                            )}
                        >
                            <Icon
                                className="h-3.5 w-3.5"
                                weight={!isCollapsed && activeTab === tabId ? 'duotone' : 'regular'}
                            />
                            {label}
                        </button>
                    )
                )}
            </div>

            {/* Control Buttons */}
            <TooltipProvider delayDuration={300}>
                <div className="flex items-center gap-0.5">
                    {/* Toggle Panel */}
                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8 text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800"
                                onClick={togglePanel}
                                aria-expanded={!isCollapsed}
                                aria-label={isCollapsed ? 'Expand panel' : 'Collapse panel'}
                            >
                                {isCollapsed ? (
                                    <CaretUp className="h-3.5 w-3.5" />
                                ) : (
                                    <CaretDown className="h-3.5 w-3.5" />
                                )}
                            </Button>
                        </TooltipTrigger>
                        <TooltipContent side="top" className="text-xs">
                            {isCollapsed ? 'Expand' : 'Collapse'} <kbd className="ml-1 text-zinc-400">Cmd+J</kbd>
                        </TooltipContent>
                    </Tooltip>

                    {/* Maximize Panel */}
                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8 text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800"
                                onClick={toggleMaximize}
                                aria-pressed={isMaximized}
                                aria-label={isMaximized ? 'Restore panel' : 'Maximize panel'}
                            >
                                {isMaximized ? (
                                    <CornersIn className="h-3.5 w-3.5" />
                                ) : (
                                    <CornersOut className="h-3.5 w-3.5" />
                                )}
                            </Button>
                        </TooltipTrigger>
                        <TooltipContent side="top" className="text-xs">
                            {isMaximized ? 'Restore' : 'Maximize'}
                        </TooltipContent>
                    </Tooltip>

                    {/* Close Panel */}
                    <Tooltip>
                        <TooltipTrigger asChild>
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8 text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800"
                                onClick={() => setCollapsed(true)}
                                aria-label="Close panel"
                            >
                                <X className="h-3.5 w-3.5" />
                            </Button>
                        </TooltipTrigger>
                        <TooltipContent side="top" className="text-xs">
                            Close <kbd className="ml-1 text-zinc-400">Esc</kbd>
                        </TooltipContent>
                    </Tooltip>
                </div>
            </TooltipProvider>
        </div>
    );
}
