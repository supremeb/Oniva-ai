'use client';

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { useShallow } from 'zustand/react/shallow';

// ============ Types ============
interface WorkflowBuilderLayoutState {
    // Panel collapsed state
    isChatCollapsed: boolean;
    isConfigCollapsed: boolean;
}

interface WorkflowBuilderLayoutActions {
    // Collapse actions
    toggleChatPanel: () => void;
    toggleConfigPanel: () => void;
    setChatCollapsed: (collapsed: boolean) => void;
    setConfigCollapsed: (collapsed: boolean) => void;

    // Reset
    reset: () => void;
}

type WorkflowBuilderLayoutStore = WorkflowBuilderLayoutState & WorkflowBuilderLayoutActions;

// ============ Constants ============
export const WORKFLOW_PANEL_DEFAULTS = {
    // Fixed widths in pixels
    chatWidth: 320,        // Chat panel width when expanded
    configWidth: 350,      // Config panel width when expanded
    collapsedWidth: 48,    // Width when collapsed (just the button)
} as const;

// ============ Initial State ============
const initialState: WorkflowBuilderLayoutState = {
    isChatCollapsed: false,
    isConfigCollapsed: false,
};

// ============ Store ============
export const useWorkflowBuilderLayoutStore = create<WorkflowBuilderLayoutStore>()(
    persist(
        (set, get) => ({
            ...initialState,

            // Collapse actions
            toggleChatPanel: () => {
                set({ isChatCollapsed: !get().isChatCollapsed });
            },

            toggleConfigPanel: () => {
                set({ isConfigCollapsed: !get().isConfigCollapsed });
            },

            setChatCollapsed: (collapsed) => {
                set({ isChatCollapsed: collapsed });
            },

            setConfigCollapsed: (collapsed) => {
                set({ isConfigCollapsed: collapsed });
            },

            // Reset
            reset: () => set(initialState),
        }),
        {
            name: 'oniva:workflow-builder-layout:v1',
            storage: createJSONStorage(() => localStorage),
            // Persist collapse state
            partialize: (state) => ({
                isChatCollapsed: state.isChatCollapsed,
                isConfigCollapsed: state.isConfigCollapsed,
            }),
        }
    )
);

// ============ Custom Hooks ============

/**
 * Use this hook for full layout state and actions
 */
export const useWorkflowBuilderLayout = () =>
    useWorkflowBuilderLayoutStore(
        useShallow((state) => ({
            isChatCollapsed: state.isChatCollapsed,
            isConfigCollapsed: state.isConfigCollapsed,
            toggleChatPanel: state.toggleChatPanel,
            toggleConfigPanel: state.toggleConfigPanel,
            setChatCollapsed: state.setChatCollapsed,
            setConfigCollapsed: state.setConfigCollapsed,
        }))
    );

/**
 * Use this hook when you only need layout actions
 */
export const useWorkflowBuilderLayoutActions = () =>
    useWorkflowBuilderLayoutStore(
        useShallow((state) => ({
            toggleChatPanel: state.toggleChatPanel,
            toggleConfigPanel: state.toggleConfigPanel,
            setChatCollapsed: state.setChatCollapsed,
            setConfigCollapsed: state.setConfigCollapsed,
            reset: state.reset,
        }))
    );

// Export types
export type { WorkflowBuilderLayoutState, WorkflowBuilderLayoutActions, WorkflowBuilderLayoutStore };
