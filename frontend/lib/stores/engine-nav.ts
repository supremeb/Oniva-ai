import { create } from 'zustand';

/**
 * Explicit "open this engine conversation" requests from the sidebar. `nonce` is the
 * remount key for the chat page, so URL writes made by EngineChat itself never remount it.
 */
interface EngineNavState {
    conversationId: string | null;
    nonce: number;
    open: (conversationId: string | null) => void;
}

export const useEngineNav = create<EngineNavState>((set) => ({
    conversationId: null,
    nonce: 0,
    open: (conversationId) => set((s) => ({ conversationId, nonce: s.nonce + 1 })),
}));
