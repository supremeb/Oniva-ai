import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  getChatSessions,
  getChatSession,
  updateChatSession,
  deleteChatSession,
} from '@/lib/api/chat-sessions';
import type { ChatSessionUpdate } from '@/lib/types/chat-panels';

export function useChatSessionsList() {
  return useQuery({
    queryKey: ['chat-sessions'],
    queryFn: () => getChatSessions({ page_size: 50 }),
    select: (data) => data.sessions,
  });
}

export function useChatSessionDetail(sessionId: string | null) {
  return useQuery({
    queryKey: ['chat-session', sessionId],
    queryFn: () => getChatSession(sessionId!),
    enabled: !!sessionId,
    retry: (failureCount, error) => {
      // Don't retry 404s (session not yet persisted to backend)
      if (error instanceof Error && error.message.includes('404')) return false;
      return failureCount < 2;
    },
    staleTime: 5000, // Avoid refetching too aggressively
  });
}

export function useUpdateChatSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ChatSessionUpdate }) =>
      updateChatSession(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['chat-sessions'] });
    },
  });
}

export function useDeleteChatSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteChatSession,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['chat-sessions'] });
    },
  });
}
