'use client';

import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  ClockCounterClockwise,
  ChatCircleDots,
  Trash,
  PushPin,
  CircleNotch,
} from '@phosphor-icons/react';
import {
  getRecentAgentSessions,
  getRecentWorkflowSessions,
  deleteChatSession,
  updateChatSession,
} from '@/lib/api/chat-sessions';
import { useChatPanelStore } from '@/lib/stores/chat-panel';
import type { ChatSession } from '@/lib/types/chat-panels';
import { cn } from '@/lib/utils';
import { formatDistanceToNow, parseISO } from 'date-fns';

interface ChatHistoryProps {
  contextType: 'agent' | 'workflow';
  contextId: string;
  contextName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelectSession?: (session: ChatSession) => void;
}

export function ChatHistory({
  contextType,
  contextId,
  contextName,
  open,
  onOpenChange,
  onSelectSession,
}: ChatHistoryProps) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const contextKey = `${contextType}:${contextId}`;
  const { setActiveSession, clearSession } = useChatPanelStore();

  // Fetch sessions when dialog opens
  useEffect(() => {
    if (!open) return;

    const fetchSessions = async () => {
      setLoading(true);
      setError(null);

      try {
        const response =
          contextType === 'agent'
            ? await getRecentAgentSessions(contextId, 20)
            : await getRecentWorkflowSessions(contextId, 20);

        setSessions(response.sessions);
      } catch (e) {
        console.error('Failed to fetch sessions:', e);
        setError('Failed to load chat history');
      } finally {
        setLoading(false);
      }
    };

    fetchSessions();
  }, [open, contextType, contextId]);

  const handleSelectSession = (session: ChatSession) => {
    setActiveSession(contextKey, session);
    onSelectSession?.(session);
    onOpenChange(false);
  };

  const handleDeleteSession = async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setDeletingId(sessionId);

    try {
      await deleteChatSession(sessionId);
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));

      // Clear from store if this was the active session
      const activeSession = useChatPanelStore.getState().activeSessions[contextKey];
      if (activeSession?.id === sessionId) {
        clearSession(contextKey);
      }
    } catch (e) {
      console.error('Failed to delete session:', e);
    } finally {
      setDeletingId(null);
    }
  };

  const handleTogglePin = async (session: ChatSession, e: React.MouseEvent) => {
    e.stopPropagation();

    try {
      const updated = await updateChatSession(session.id, {
        is_pinned: !session.is_pinned,
      });
      setSessions((prev) =>
        prev.map((s) => (s.id === session.id ? updated : s))
      );
    } catch (e) {
      console.error('Failed to update session:', e);
    }
  };

  const formatDate = (dateString: string) => {
    try {
      return formatDistanceToNow(parseISO(dateString), { addSuffix: true });
    } catch {
      return 'Unknown';
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <ClockCounterClockwise className="h-5 w-5" />
            Chat History
          </DialogTitle>
          <DialogDescription>
            Previous conversations with {contextName}
          </DialogDescription>
        </DialogHeader>

        <ScrollArea className="max-h-[400px] mt-4">
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <CircleNotch className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : error ? (
            <div className="text-center py-8 text-destructive">{error}</div>
          ) : sessions.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <ChatCircleDots className="h-12 w-12 mx-auto mb-4 opacity-50" />
              <p>No previous conversations</p>
            </div>
          ) : (
            <div className="space-y-2">
              {sessions.map((session) => (
                <div
                  key={session.id}
                  onClick={() => handleSelectSession(session)}
                  className={cn(
                    'p-3 rounded-lg border cursor-pointer transition-colors',
                    'hover:bg-accent hover:border-accent-foreground/20',
                    session.is_pinned && 'border-primary/30 bg-primary/5'
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        {session.is_pinned && (
                          <PushPin
                            className="h-3 w-3 text-primary"
                            weight="fill"
                          />
                        )}
                        <span className="font-medium text-sm truncate">
                          {session.title || 'Untitled Chat'}
                        </span>
                      </div>
                      <p className="text-xs text-muted-foreground mt-1">
                        {session.message_count} messages •{' '}
                        {formatDate(session.updated_at)}
                      </p>
                      {session.messages[0] && (
                        <p className="text-xs text-muted-foreground mt-1 truncate">
                          {session.messages[0].content.slice(0, 60)}...
                        </p>
                      )}
                    </div>
                    <div className="flex items-center gap-1">
                      {session.is_draft && (
                        <Badge variant="secondary" className="text-xs">
                          Draft
                        </Badge>
                      )}
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8"
                        onClick={(e) => handleTogglePin(session, e)}
                        title={session.is_pinned ? 'Unpin' : 'Pin'}
                      >
                        <PushPin
                          className={cn(
                            'h-4 w-4',
                            session.is_pinned && 'text-primary'
                          )}
                          weight={session.is_pinned ? 'fill' : 'regular'}
                        />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 text-destructive hover:text-destructive"
                        onClick={(e) => handleDeleteSession(session.id, e)}
                        disabled={deletingId === session.id}
                        title="Delete"
                      >
                        {deletingId === session.id ? (
                          <CircleNotch className="h-4 w-4 animate-spin" />
                        ) : (
                          <Trash className="h-4 w-4" />
                        )}
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </ScrollArea>

        <div className="flex justify-end mt-4">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
