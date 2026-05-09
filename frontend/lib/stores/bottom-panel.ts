'use client';

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { useShallow } from 'zustand/react/shallow';

// ============ Types ============
export type BottomPanelTab = 'logs' | 'monitoring' | 'api';

interface BottomPanelState {
    // State
    isCollapsed: boolean;
    isMaximized: boolean;
    panelSize: number; // Percentage (15-60)
    activeTab: BottomPanelTab;
}

interface BottomPanelActions {
    // Actions
    setCollapsed: (collapsed: boolean) => void;
    togglePanel: () => void;
    setMaximized: (maximized: boolean) => void;
    toggleMaximize: () => void;
    setPanelSize: (size: number) => void;
    setActiveTab: (tab: BottomPanelTab) => void;
    expandToTab: (tab: BottomPanelTab) => void;
    reset: () => void;
}

type BottomPanelStore = BottomPanelState & BottomPanelActions;

// ============ Constants ============
export const PANEL_DEFAULTS = {
    defaultSize: 35,  // Expanded panel height (35% of screen)
    minSize: 35,      // Minimum when expanded (can't resize below 35%)
    maxSize: 60,
    maximizedSize: 100, // True full screen - hides canvas completely
    collapsedSize: 8, // ~72px for thicker header with py-5 padding
} as const;

// ============ Initial State ============
const initialState: BottomPanelState = {
    isCollapsed: true, // Start collapsed by default
    isMaximized: false,
    panelSize: PANEL_DEFAULTS.defaultSize, // 35% when expanded
    activeTab: 'logs',
};

// ============ Store ============
export const useBottomPanelStore = create<BottomPanelStore>()(
    persist(
        (set, get) => ({
            ...initialState,

            // Actions
            setCollapsed: (isCollapsed) => set({ isCollapsed, isMaximized: false }),

            togglePanel: () => set((state) => ({ isCollapsed: !state.isCollapsed, isMaximized: false })),

            setMaximized: (isMaximized) => set({ isMaximized, isCollapsed: false }),

            toggleMaximize: () => set((state) => ({
                isMaximized: !state.isMaximized,
                isCollapsed: false
            })),

            setPanelSize: (panelSize) => {
                // Clamp to valid range
                const clampedSize = Math.max(
                    PANEL_DEFAULTS.minSize,
                    Math.min(PANEL_DEFAULTS.maxSize, panelSize)
                );
                set({ panelSize: clampedSize });
            },

            setActiveTab: (activeTab) => set({ activeTab }),

            // Expand panel and switch to specific tab
            expandToTab: (tab) => set({ isCollapsed: false, activeTab: tab }),

            reset: () => set(initialState),
        }),
        {
            name: 'oniva:bottom-panel:v2', // Bump version to clear old state
            storage: createJSONStorage(() => localStorage),
            // Only persist size and tab - NOT isCollapsed (always start collapsed)
            partialize: (state) => ({
                panelSize: state.panelSize,
                activeTab: state.activeTab,
            }),
        }
    )
);

// ============ Selectors ============
export const selectPanelState = (state: BottomPanelStore) => ({
    isCollapsed: state.isCollapsed,
    isMaximized: state.isMaximized,
    panelSize: state.panelSize,
    activeTab: state.activeTab,
});

export const selectPanelActions = (state: BottomPanelStore) => ({
    setCollapsed: state.setCollapsed,
    togglePanel: state.togglePanel,
    setMaximized: state.setMaximized,
    toggleMaximize: state.toggleMaximize,
    setPanelSize: state.setPanelSize,
    setActiveTab: state.setActiveTab,
    expandToTab: state.expandToTab,
    reset: state.reset,
});

// ============ Custom Hooks ============

/**
 * Use this hook for full panel state and actions
 */
export const useBottomPanel = () =>
    useBottomPanelStore(
        useShallow((state) => ({
            isCollapsed: state.isCollapsed,
            isMaximized: state.isMaximized,
            panelSize: state.panelSize,
            activeTab: state.activeTab,
            setCollapsed: state.setCollapsed,
            togglePanel: state.togglePanel,
            setMaximized: state.setMaximized,
            toggleMaximize: state.toggleMaximize,
            setPanelSize: state.setPanelSize,
            setActiveTab: state.setActiveTab,
            expandToTab: state.expandToTab,
        }))
    );

/**
 * Use this hook when you only need panel actions (no re-renders on state change)
 */
export const useBottomPanelActions = () =>
    useBottomPanelStore(useShallow(selectPanelActions));

// Export types
export type { BottomPanelState, BottomPanelActions, BottomPanelStore };
