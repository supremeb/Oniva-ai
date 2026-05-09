'use client';

import { useState, useEffect, useCallback } from 'react';
import type { ChatMessage } from '@/lib/models/chat-messages';
import { createWelcomeMessage } from '@/lib/models/chat-messages';
import {
  loadChatHistory,
  saveChatHistory,
  clearChatHistory,
} from '@/lib/chat-persistence';

interface UsePersistedChatOptions {
  workflowId: string;
  stepCount?: number;
}

interface UsePersistedChatReturn {
  messages: ChatMessage[];
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  addMessage: (message: ChatMessage) => void;
  updateMessage: (id: string, updates: Partial<ChatMessage>) => void;
  removeMessage: (id: string) => void;
  clearChat: () => void;
  isLoaded: boolean;
}

/**
 * Hook for managing persisted chat messages.
 *
 * Features:
 * - Loads history from localStorage on mount
 * - Auto-saves on every message change (debounced)
 * - Provides clear function for resetting chat
 * - Creates welcome message if no history exists
 */
export function usePersistedChat({
  workflowId,
  stepCount = 0,
}: UsePersistedChatOptions): UsePersistedChatReturn {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoaded, setIsLoaded] = useState(false);

  // Load history on mount (only when workflowId changes, NOT stepCount)
  // stepCount in deps caused reload-from-localStorage after Apply Changes
  // overwrote the in-memory "applied: true" metadata before debounced save
  useEffect(() => {
    const history = loadChatHistory(workflowId);

    if (history.length > 0) {
      setMessages(history);
    } else {
      // No history - show welcome message
      setMessages([createWelcomeMessage(stepCount)]);
    }

    setIsLoaded(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId]);

  // Auto-save when messages change (debounced effect)
  useEffect(() => {
    if (!isLoaded) return; // Don't save before initial load

    // Debounce saves to avoid thrashing localStorage
    const timeout = setTimeout(() => {
      saveChatHistory(workflowId, messages);
    }, 500);

    return () => clearTimeout(timeout);
  }, [workflowId, messages, isLoaded]);

  // Add a single message
  const addMessage = useCallback((message: ChatMessage) => {
    setMessages((prev) => [...prev, message]);
  }, []);

  // Update a message by ID
  const updateMessage = useCallback(
    (id: string, updates: Partial<ChatMessage>) => {
      setMessages((prev) =>
        prev.map((msg) => (msg.id === id ? { ...msg, ...updates } : msg))
      );
    },
    []
  );

  // Remove a message by ID
  const removeMessage = useCallback((id: string) => {
    setMessages((prev) => prev.filter((msg) => msg.id !== id));
  }, []);

  // Clear all chat history
  const clearChat = useCallback(() => {
    clearChatHistory(workflowId);
    setMessages([createWelcomeMessage(stepCount)]);
  }, [workflowId, stepCount]);

  return {
    messages,
    setMessages,
    addMessage,
    updateMessage,
    removeMessage,
    clearChat,
    isLoaded,
  };
}
