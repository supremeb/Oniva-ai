import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { ChatSession, ChatMessage } from '@/lib/types/chat-panels';

interface DraftMessage {
  agentId?: string;
  workflowId?: string;
  content: string;
}

interface ChatPanelState {
  // Active sessions
  activeSessions: Record<string, ChatSession | null>; // keyed by context (agent:{id} or workflow:{id})

  // Draft messages (unsaved input per context)
  draftMessages: Record<string, DraftMessage>;

  // Current draft mode state
  draftModeContexts: Set<string>; // contexts that have unsaved changes

  // UI state
  isChatPanelOpen: Record<string, boolean>;
  chatPanelWidth: Record<string, number>; // persisted panel widths

  // Streaming state
  streamingContexts: Set<string>;
}

interface ChatPanelActions {
  // Session management
  setActiveSession: (contextKey: string, session: ChatSession | null) => void;
  updateSessionMessages: (contextKey: string, messages: ChatMessage[]) => void;
  addMessageToSession: (contextKey: string, message: ChatMessage) => void;
  clearSession: (contextKey: string) => void;

  // Draft management
  setDraftMessage: (contextKey: string, content: string, agentId?: string, workflowId?: string) => void;
  clearDraftMessage: (contextKey: string) => void;

  // Draft mode tracking
  setDraftMode: (contextKey: string, isDraft: boolean) => void;
  hasDraftChanges: (contextKey: string) => boolean;

  // UI state
  toggleChatPanel: (contextKey: string) => void;
  setChatPanelOpen: (contextKey: string, isOpen: boolean) => void;
  setChatPanelWidth: (contextKey: string, width: number) => void;

  // Streaming
  setStreaming: (contextKey: string, isStreaming: boolean) => void;
  isStreaming: (contextKey: string) => boolean;

  // Helpers
  getContextKey: (type: 'agent' | 'workflow', id: string) => string;
  reset: () => void;
}

type ChatPanelStore = ChatPanelState & ChatPanelActions;

const initialState: ChatPanelState = {
  activeSessions: {},
  draftMessages: {},
  draftModeContexts: new Set(),
  isChatPanelOpen: {},
  chatPanelWidth: {},
  streamingContexts: new Set(),
};

export const useChatPanelStore = create<ChatPanelStore>()(
  persist(
    (set, get) => ({
      ...initialState,

      // Session management
      setActiveSession: (contextKey, session) =>
        set((state) => ({
          activeSessions: { ...state.activeSessions, [contextKey]: session },
        })),

      updateSessionMessages: (contextKey, messages) =>
        set((state) => {
          const session = state.activeSessions[contextKey];
          if (!session) return state;

          return {
            activeSessions: {
              ...state.activeSessions,
              [contextKey]: {
                ...session,
                messages,
                message_count: messages.length,
                updated_at: new Date().toISOString(),
              },
            },
          };
        }),

      addMessageToSession: (contextKey, message) =>
        set((state) => {
          const session = state.activeSessions[contextKey];
          if (!session) return state;

          const newMessages = [...session.messages, message];
          return {
            activeSessions: {
              ...state.activeSessions,
              [contextKey]: {
                ...session,
                messages: newMessages,
                message_count: newMessages.length,
                last_message_at: message.timestamp,
                updated_at: new Date().toISOString(),
              },
            },
          };
        }),

      clearSession: (contextKey) =>
        set((state) => {
          const { [contextKey]: _, ...rest } = state.activeSessions;
          const newDraftContexts = new Set(state.draftModeContexts);
          newDraftContexts.delete(contextKey);

          return {
            activeSessions: rest,
            draftModeContexts: newDraftContexts,
          };
        }),

      // Draft management
      setDraftMessage: (contextKey, content, agentId, workflowId) =>
        set((state) => ({
          draftMessages: {
            ...state.draftMessages,
            [contextKey]: { content, agentId, workflowId },
          },
        })),

      clearDraftMessage: (contextKey) =>
        set((state) => {
          const { [contextKey]: _, ...rest } = state.draftMessages;
          return { draftMessages: rest };
        }),

      // Draft mode tracking
      setDraftMode: (contextKey, isDraft) =>
        set((state) => {
          const newSet = new Set(state.draftModeContexts);
          if (isDraft) {
            newSet.add(contextKey);
          } else {
            newSet.delete(contextKey);
          }
          return { draftModeContexts: newSet };
        }),

      hasDraftChanges: (contextKey) => {
        const state = get();
        return state.draftModeContexts.has(contextKey) ||
               Boolean(state.draftMessages[contextKey]?.content);
      },

      // UI state
      toggleChatPanel: (contextKey) =>
        set((state) => ({
          isChatPanelOpen: {
            ...state.isChatPanelOpen,
            [contextKey]: !state.isChatPanelOpen[contextKey],
          },
        })),

      setChatPanelOpen: (contextKey, isOpen) =>
        set((state) => ({
          isChatPanelOpen: { ...state.isChatPanelOpen, [contextKey]: isOpen },
        })),

      setChatPanelWidth: (contextKey, width) =>
        set((state) => ({
          chatPanelWidth: { ...state.chatPanelWidth, [contextKey]: width },
        })),

      // Streaming
      setStreaming: (contextKey, isStreaming) =>
        set((state) => {
          const newSet = new Set(state.streamingContexts);
          if (isStreaming) {
            newSet.add(contextKey);
          } else {
            newSet.delete(contextKey);
          }
          return { streamingContexts: newSet };
        }),

      isStreaming: (contextKey) => get().streamingContexts.has(contextKey),

      // Helpers
      getContextKey: (type, id) => `${type}:${id}`,

      reset: () => set(initialState),
    }),
    {
      name: 'oniva-chat-panel',
      partialize: (state) => ({
        // Only persist panel widths and open states
        chatPanelWidth: state.chatPanelWidth,
        isChatPanelOpen: state.isChatPanelOpen,
      }),
      // Handle Set serialization
      storage: {
        getItem: (name) => {
          const str = localStorage.getItem(name);
          if (!str) return null;
          return JSON.parse(str);
        },
        setItem: (name, value) => {
          localStorage.setItem(name, JSON.stringify(value));
        },
        removeItem: (name) => localStorage.removeItem(name),
      },
    }
  )
);

// Selectors
export const selectActiveSession = (contextKey: string) => (state: ChatPanelStore) =>
  state.activeSessions[contextKey];

export const selectDraftMessage = (contextKey: string) => (state: ChatPanelStore) =>
  state.draftMessages[contextKey];

export const selectIsChatPanelOpen = (contextKey: string) => (state: ChatPanelStore) =>
  state.isChatPanelOpen[contextKey] ?? false;

export const selectChatPanelWidth = (contextKey: string) => (state: ChatPanelStore) =>
  state.chatPanelWidth[contextKey] ?? 50; // Default 50%
