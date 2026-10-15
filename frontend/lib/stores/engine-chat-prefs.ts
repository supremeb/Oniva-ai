import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

/**
 * Engine chat preferences (KTD12): only UI conveniences live in the browser.
 * Unsent composer drafts are kept per conversation (`NEW_DRAFT_KEY` before one
 * exists); submitted messages are never written here, the server view owns them.
 */
export const NEW_DRAFT_KEY = 'new';

interface EngineChatPrefs {
  drafts: Record<string, string>;
  setDraft: (key: string, text: string) => void;
  clearDraft: (key: string) => void;
}

export const useEngineChatPrefs = create<EngineChatPrefs>()(
  persist(
    (set) => ({
      drafts: {},
      setDraft: (key, text) =>
        set((s) => {
          const drafts = { ...s.drafts };
          if (text) drafts[key] = text;
          else delete drafts[key];
          return { drafts };
        }),
      clearDraft: (key) =>
        set((s) => {
          const drafts = { ...s.drafts };
          delete drafts[key];
          return { drafts };
        }),
    }),
    {
      name: 'oniva-engine-chat-prefs',
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({ drafts: s.drafts }),
    }
  )
);
