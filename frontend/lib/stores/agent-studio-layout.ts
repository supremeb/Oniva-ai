'use client';

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { useShallow } from 'zustand/react/shallow';

// ============ Types ============
export type AgentStudioTab = 'resources' | 'graders' | 'settings' | 'background';

interface AgentStudioLayoutState {
    // Panel sizing
    chatPanelSize: number; // Percentage (30-70)
    settingsPanelSize: number; // Percentage (30-70)

    // Tab state
    activeTab: AgentStudioTab;

    // Collapsible sections state
    collapsedSections: string[];
}

interface AgentStudioLayoutActions {
    // Panel actions
    setChatPanelSize: (size: number) => void;
    setSettingsPanelSize: (size: number) => void;
    resetPanelSizes: () => void;

    // Tab actions
    setActiveTab: (tab: AgentStudioTab) => void;

    // Section actions
    toggleSection: (sectionId: string) => void;
    setSectionCollapsed: (sectionId: string, collapsed: boolean) => void;
    isSectionCollapsed: (sectionId: string) => boolean;

    // Reset
    reset: () => void;
}

type AgentStudioLayoutStore = AgentStudioLayoutState & AgentStudioLayoutActions;

// ============ Constants ============
export const PANEL_DEFAULTS = {
    chatSize: 65, // 65% for editor
    settingsSize: 35, // 35% for sidebar
    minChatSize: 50,
    maxChatSize: 80,
    minSettingsSize: 20,
    maxSettingsSize: 50,
} as const;

// V1→V2 tab name migration for existing localStorage users
const TAB_MIGRATION: Record<string, AgentStudioTab> = {
    overview: 'resources',
    tests: 'graders',
    versions: 'settings',
};

// Default collapsed state for sections
const DEFAULT_COLLAPSED_SECTIONS: string[] = []; // All expanded by default

// ============ Initial State ============
const initialState: AgentStudioLayoutState = {
    chatPanelSize: PANEL_DEFAULTS.chatSize,
    settingsPanelSize: PANEL_DEFAULTS.settingsSize,
    activeTab: 'settings',
    collapsedSections: DEFAULT_COLLAPSED_SECTIONS,
};

// ============ Store ============
export const useAgentStudioLayoutStore = create<AgentStudioLayoutStore>()(
    persist(
        (set, get) => ({
            ...initialState,

            // Panel actions
            setChatPanelSize: (size) => {
                const clampedSize = Math.max(
                    PANEL_DEFAULTS.minChatSize,
                    Math.min(PANEL_DEFAULTS.maxChatSize, size)
                );
                set({
                    chatPanelSize: clampedSize,
                    settingsPanelSize: 100 - clampedSize
                });
            },

            setSettingsPanelSize: (size) => {
                const clampedSize = Math.max(
                    PANEL_DEFAULTS.minSettingsSize,
                    Math.min(PANEL_DEFAULTS.maxSettingsSize, size)
                );
                set({
                    settingsPanelSize: clampedSize,
                    chatPanelSize: 100 - clampedSize
                });
            },

            resetPanelSizes: () => {
                set({
                    chatPanelSize: PANEL_DEFAULTS.chatSize,
                    settingsPanelSize: PANEL_DEFAULTS.settingsSize,
                });
            },

            // Tab actions
            setActiveTab: (activeTab) => set({ activeTab }),

            // Section actions
            toggleSection: (sectionId) => {
                const { collapsedSections } = get();
                const isCollapsed = collapsedSections.includes(sectionId);
                set({
                    collapsedSections: isCollapsed
                        ? collapsedSections.filter((id) => id !== sectionId)
                        : [...collapsedSections, sectionId],
                });
            },

            setSectionCollapsed: (sectionId, collapsed) => {
                const { collapsedSections } = get();
                const isCollapsed = collapsedSections.includes(sectionId);

                if (collapsed && !isCollapsed) {
                    set({ collapsedSections: [...collapsedSections, sectionId] });
                } else if (!collapsed && isCollapsed) {
                    set({ collapsedSections: collapsedSections.filter((id) => id !== sectionId) });
                }
            },

            isSectionCollapsed: (sectionId) => {
                return get().collapsedSections.includes(sectionId);
            },

            // Reset
            reset: () => set(initialState),
        }),
        {
            name: 'oniva:agent-studio-layout:v1',
            storage: createJSONStorage(() => localStorage),
            // Persist all layout preferences
            partialize: (state) => ({
                chatPanelSize: state.chatPanelSize,
                settingsPanelSize: state.settingsPanelSize,
                activeTab: state.activeTab,
                collapsedSections: state.collapsedSections,
            }),
            // Migrate V1 tab names → V2 on hydration
            merge: (persisted, current) => ({
                ...current,
                ...(persisted as Partial<AgentStudioLayoutState>),
                activeTab: TAB_MIGRATION[(persisted as AgentStudioLayoutState)?.activeTab]
                    ?? (persisted as AgentStudioLayoutState)?.activeTab
                    ?? 'settings',
            }),
        }
    )
);

// ============ Selectors ============
export const selectLayoutState = (state: AgentStudioLayoutStore) => ({
    chatPanelSize: state.chatPanelSize,
    settingsPanelSize: state.settingsPanelSize,
    activeTab: state.activeTab,
    collapsedSections: state.collapsedSections,
});

export const selectLayoutActions = (state: AgentStudioLayoutStore) => ({
    setChatPanelSize: state.setChatPanelSize,
    setSettingsPanelSize: state.setSettingsPanelSize,
    resetPanelSizes: state.resetPanelSizes,
    setActiveTab: state.setActiveTab,
    toggleSection: state.toggleSection,
    setSectionCollapsed: state.setSectionCollapsed,
    reset: state.reset,
});

// ============ Custom Hooks ============

/**
 * Use this hook for full layout state and actions
 */
export const useAgentStudioLayout = () =>
    useAgentStudioLayoutStore(
        useShallow((state) => ({
            chatPanelSize: state.chatPanelSize,
            settingsPanelSize: state.settingsPanelSize,
            activeTab: state.activeTab,
            collapsedSections: state.collapsedSections,
            setChatPanelSize: state.setChatPanelSize,
            setSettingsPanelSize: state.setSettingsPanelSize,
            resetPanelSizes: state.resetPanelSizes,
            setActiveTab: state.setActiveTab,
            toggleSection: state.toggleSection,
            setSectionCollapsed: state.setSectionCollapsed,
            isSectionCollapsed: state.isSectionCollapsed,
        }))
    );

/**
 * Use this hook when you only need layout actions (no re-renders on state change)
 */
export const useAgentStudioLayoutActions = () =>
    useAgentStudioLayoutStore(useShallow(selectLayoutActions));

/**
 * Use this hook for just panel sizing
 */
export const useAgentStudioPanelSize = () =>
    useAgentStudioLayoutStore(
        useShallow((state) => ({
            chatPanelSize: state.chatPanelSize,
            settingsPanelSize: state.settingsPanelSize,
            setChatPanelSize: state.setChatPanelSize,
            setSettingsPanelSize: state.setSettingsPanelSize,
            resetPanelSizes: state.resetPanelSizes,
        }))
    );

// Export types
export type { AgentStudioLayoutState, AgentStudioLayoutActions, AgentStudioLayoutStore };
