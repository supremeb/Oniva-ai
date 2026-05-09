import { create } from 'zustand';

/**
 * Chat UI Store — transient layout state.
 *
 * Separate from chat-sessions.ts which holds domain/session data.
 * This store manages visual-only concerns like panel visibility.
 */

export type RightPanelContent =
  | { tab: 'files' }
  | { tab: 'sources' }
  | { tab: 'artifact'; path: string };

interface ChatUIStore {
  // Right panel
  rightPanelOpen: boolean;
  rightPanelContent: RightPanelContent;
  toggleRightPanel: () => void;
  openRightPanel: () => void;
  closeRightPanel: () => void;
  setRightPanelContent: (content: RightPanelContent) => void;
  openArtifact: (path: string) => void;
}

export const useChatUIStore = create<ChatUIStore>((set) => ({
  rightPanelOpen: false,
  rightPanelContent: { tab: 'files' },

  toggleRightPanel: () => set((s) => ({ rightPanelOpen: !s.rightPanelOpen })),
  openRightPanel: () => set({ rightPanelOpen: true }),
  closeRightPanel: () => set({ rightPanelOpen: false }),

  setRightPanelContent: (content) => set({ rightPanelContent: content }),

  openArtifact: (path) =>
    set({ rightPanelOpen: true, rightPanelContent: { tab: 'artifact', path } }),
}));
