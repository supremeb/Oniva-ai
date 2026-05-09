'use client';

import { useCallback, useEffect, useRef } from 'react';
import { type PanelImperativeHandle, type PanelSize } from 'react-resizable-panels';
import {
    ResizablePanelGroup,
    ResizablePanel,
    ResizableHandle,
} from '@/components/ui/resizable';
import { BottomPanelHeader } from './BottomPanelHeader';
import { BottomPanelContent } from './BottomPanelContent';
import { useBottomPanel, PANEL_DEFAULTS } from '@/lib/stores/bottom-panel';
import { cn } from '@/lib/utils';

interface BottomPanelLayoutProps {
    children: React.ReactNode;
    workflowId: string;
    className?: string;
    /** Whether to show the bottom panel (false for Chat/Versions tabs) */
    showPanel?: boolean;
}

export function BottomPanelLayout({
    children,
    workflowId,
    className,
    showPanel = true,
}: BottomPanelLayoutProps) {
    const panelRef = useRef<PanelImperativeHandle>(null);
    const isProgrammaticChange = useRef(false);
    const prevCollapsedRef = useRef<boolean | null>(null);

    const {
        isCollapsed,
        isMaximized,
        setCollapsed,
        setMaximized,
        togglePanel,
        setPanelSize,
    } = useBottomPanel();

    // Sync panel imperative handle with Zustand state changes
    useEffect(() => {
        // Don't manage panel when maximized (using overlay mode)
        if (isMaximized) return;

        const panel = panelRef.current;
        if (!panel) return;

        // On initial render, collapse if store says collapsed
        if (prevCollapsedRef.current === null) {
            prevCollapsedRef.current = isCollapsed;
            if (isCollapsed) {
                isProgrammaticChange.current = true;
                panel.collapse();
                setTimeout(() => {
                    isProgrammaticChange.current = false;
                }, 100);
            }
            return;
        }

        // Skip if state hasn't changed
        if (prevCollapsedRef.current === isCollapsed) return;

        isProgrammaticChange.current = true;
        prevCollapsedRef.current = isCollapsed;

        if (isCollapsed) {
            panel.collapse();
        } else {
            panel.expand();
        }

        // Reset flag after animation
        const timer = setTimeout(() => {
            isProgrammaticChange.current = false;
        }, 100);

        return () => clearTimeout(timer);
    }, [isCollapsed, isMaximized]);

    // Keyboard shortcuts: Cmd+J to toggle, Escape to collapse/restore
    useEffect(() => {
        if (!showPanel) return;

        const handleKeyDown = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'j') {
                e.preventDefault();
                togglePanel();
            }
            if (e.key === 'Escape') {
                const activeElement = document.activeElement;
                const isInInput = activeElement?.tagName === 'INPUT' ||
                    activeElement?.tagName === 'TEXTAREA' ||
                    activeElement?.closest('[role="dialog"]');
                if (!isInInput) {
                    e.preventDefault();
                    // If maximized, restore first
                    if (isMaximized) {
                        setMaximized(false);
                    } else if (!isCollapsed) {
                        setCollapsed(true);
                    }
                }
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [showPanel, isCollapsed, isMaximized, togglePanel, setCollapsed, setMaximized]);

    // Handle resize - detect collapse/expand and update size in store
    const handleResize = useCallback(
        (panelSize: PanelSize) => {
            if (isProgrammaticChange.current || isMaximized) return;

            const size = panelSize.asPercentage;
            const collapsed = size <= PANEL_DEFAULTS.collapsedSize + 1;

            if (collapsed && !isCollapsed) {
                setCollapsed(true);
            } else if (!collapsed && isCollapsed) {
                setCollapsed(false);
            }

            if (!collapsed) {
                setPanelSize(size);
            }
        },
        [isCollapsed, isMaximized, setCollapsed, setPanelSize]
    );

    // Double-click handle resets to default size
    const handleDoubleClick = useCallback(() => {
        const panel = panelRef.current;
        if (panel) {
            isProgrammaticChange.current = true;
            panel.resize(`${PANEL_DEFAULTS.defaultSize}%`);
            setPanelSize(PANEL_DEFAULTS.defaultSize);
            setCollapsed(false);
            setTimeout(() => {
                isProgrammaticChange.current = false;
            }, 100);
        }
    }, [setPanelSize, setCollapsed]);

    if (!showPanel) {
        return <div className={cn('h-full', className)}>{children}</div>;
    }

    // Maximized mode: Use overlay instead of resizable panels
    if (isMaximized) {
        return (
            <div className={cn('h-full relative', className)}>
                {/* Main content hidden behind maximized panel */}
                <div className="h-full opacity-0 pointer-events-none">
                    {children}
                </div>
                {/* Full-screen panel overlay */}
                <div className="absolute inset-0 z-50 bg-white dark:bg-zinc-950 flex flex-col shadow-[0_-4px_12px_-2px_rgba(0,0,0,0.08)]">
                    {/* Header */}
                    <div className="shrink-0">
                        <BottomPanelHeader />
                    </div>
                    {/* Content */}
                    <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
                        <BottomPanelContent workflowId={workflowId} className="flex-1 min-h-0" />
                    </div>
                </div>
            </div>
        );
    }

    // Normal mode: Use resizable panels
    return (
        <ResizablePanelGroup
            direction="vertical"
            className={cn('h-full', className)}
        >
            {/* Main Content */}
            <ResizablePanel
                defaultSize={`${100 - PANEL_DEFAULTS.collapsedSize}%`}
                minSize="30%"
                className="relative bg-slate-50 dark:bg-zinc-900"
            >
                {children}
            </ResizablePanel>

            {/* Resize Handle */}
            <ResizableHandle
                onDoubleClick={handleDoubleClick}
                className={cn(
                    'bg-zinc-800 hover:bg-zinc-700 transition-colors',
                    'data-[panel-group-direction=vertical]:h-1'
                )}
            />

            {/* Bottom Panel */}
            <ResizablePanel
                panelRef={panelRef}
                defaultSize={`${PANEL_DEFAULTS.collapsedSize}%`}
                minSize={`${PANEL_DEFAULTS.minSize}%`}
                maxSize={`${PANEL_DEFAULTS.maxSize}%`}
                collapsible
                collapsedSize={`${PANEL_DEFAULTS.collapsedSize}%`}
                onResize={handleResize}
                className="bg-white dark:bg-zinc-950 flex flex-col shadow-[0_-4px_12px_-2px_rgba(0,0,0,0.08)]"
            >
                {/* Header */}
                <div className="shrink-0">
                    <BottomPanelHeader />
                </div>
                {/* Content - hide when collapsed */}
                <div className={cn(
                    'flex-1 min-h-0 overflow-hidden flex flex-col',
                    isCollapsed && 'hidden'
                )}>
                    <BottomPanelContent workflowId={workflowId} className="flex-1 min-h-0" />
                </div>
            </ResizablePanel>
        </ResizablePanelGroup>
    );
}
