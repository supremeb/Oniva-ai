'use client';

/**
 * FleetChat — Rich conversation view for background agent runs.
 *
 * Uses the same ChainOfThought + ChatMessage components as the main Chat
 * for identical rendering of tool calls, thinking, and agent activity.
 *
 * Execution trace steps → ChainOfThought (tool timeline, thinking, results)
 * Operator interventions → ChatMessage (user bubbles)
 */

import { useEffect, useRef, useState } from 'react';
import { PaperPlaneRight, Robot, Exam, Link, CheckCircle, XCircle } from '@phosphor-icons/react';
import { interveneInRun } from '@/lib/api/fleet';
import { ChatMessage, type ChatMessageData } from '@/components/chat/shared/ChatMessage';
import { ChainOfThought } from '@/components/chat/shared/ChainOfThought';
import { useFleetJobStream } from '@/hooks/useFleetJobStream';
import { cn } from '@/lib/utils';

interface FleetChatProps {
  runId: string;
  agentName: string;
  status: string;
}

export function FleetChat({ runId, agentName, status: initialStatus }: FleetChatProps) {
  const { parts, toolCallMeta, status, job, isLive, loading, error } = useFleetJobStream(runId);

  const [interventions, setInterventions] = useState<ChatMessageData[]>([]);
  const [inputMsg, setInputMsg] = useState('');
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll on new parts or interventions
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [parts, interventions]);

  const handleSend = async () => {
    const text = inputMsg.trim();
    if (!text) return;
    setSending(true);

    try {
      await interveneInRun(runId, text);
      // Pessimistic: only show message after confirmed delivery
      const userMsg: ChatMessageData = {
        id: `user-${Date.now()}`,
        role: 'user',
        content: text,
      };
      setInterventions((prev) => [...prev, userMsg]);
      setInputMsg('');
    } catch {
      // Restore input so user can retry without retyping
      setInputMsg(text);
      setInterventions((prev) => [
        ...prev,
        { id: `err-${Date.now()}`, role: 'assistant', content: 'Failed to send intervention message.' },
      ]);
    }
    setSending(false);
  };

  const isRunning = status === 'running' || status === 'pending';
  const displayStatus = status || initialStatus;

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
        Loading...
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border flex items-center gap-2 shrink-0">
        <Robot className="h-4 w-4 text-primary" weight="fill" />
        <span className="text-sm font-semibold">{job?.agent_name || agentName}</span>
        <span className={cn(
          'px-1.5 py-0.5 rounded-full text-[10px] font-medium',
          displayStatus === 'running' ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400' :
          displayStatus === 'completed' ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' :
          displayStatus === 'failed' ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' :
          'bg-gray-100 text-gray-700'
        )}>
          {displayStatus}
        </span>
        {isLive && (
          <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" title="Live" />
        )}
        {job?.duration_ms && (
          <span className="text-xs text-muted-foreground ml-auto">
            {(job.duration_ms / 1000).toFixed(0)}s
          </span>
        )}
      </div>

      {/* Channel status — evaluator score + webhook delivery */}
      {!isRunning && job?.result && (job.result.score != null || job.result.action) && (
        <div className="px-4 py-2 border-b border-border flex items-center gap-4 text-xs text-muted-foreground">
          {job.result.score != null && (
            <span className={cn(
              'flex items-center gap-1',
              job.result.passed ? 'text-green-600' : 'text-amber-600'
            )}>
              <Exam className="h-3.5 w-3.5" />
              Score: {job.result.score}/100
              {job.result.passed ? ' (passed)' : ' (review)'}
            </span>
          )}
          {job.result.action === 'delivered' && (
            <span className="flex items-center gap-1 text-green-600">
              <Link className="h-3.5 w-3.5" />
              Webhook delivered
            </span>
          )}
          {job.result.action === 'delivered_without_evaluation' && (
            <span className="flex items-center gap-1">
              <CheckCircle className="h-3.5 w-3.5" />
              Delivered (no evaluation)
            </span>
          )}
          {job.result.action === 'routed_to_review' && (
            <span className="flex items-center gap-1 text-amber-600">
              <XCircle className="h-3.5 w-3.5" />
              Sent to review queue
            </span>
          )}
        </div>
      )}

      {/* Messages — agent activity + operator interventions */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4">
        {parts.length === 0 && interventions.length === 0 ? (
          <div className="text-sm text-muted-foreground text-center py-8">
            {isRunning ? 'Waiting for agent to start...' : 'No execution trace available.'}
          </div>
        ) : (
          <div className="space-y-4">
            {/* Agent activity rendered as ChainOfThought */}
            {parts.length > 0 && (
              <div className="flex gap-3 justify-start">
                <div className="flex flex-col w-full min-w-0">
                  <ChainOfThought
                    parts={parts}
                    compact
                    animated={isLive}
                    toolCallMeta={toolCallMeta}
                  />
                </div>
              </div>
            )}

            {/* Error banner */}
            {error && (
              <div className="px-3 py-2 rounded-lg bg-destructive/10 text-destructive text-sm">
                {error}
              </div>
            )}

            {/* Operator intervention messages */}
            {interventions.map((msg) => (
              <ChatMessage
                key={msg.id}
                message={msg}
                renderMarkdown={msg.role === 'assistant'}
                showAvatar={false}
                compact
              />
            ))}
          </div>
        )}
      </div>

      {/* Input — only for running jobs */}
      {isRunning && (
        <div className="border-t border-border px-4 py-3 shrink-0">
          <div className="flex items-center gap-2 max-w-3xl mx-auto">
            <input
              type="text"
              value={inputMsg}
              onChange={(e) => setInputMsg(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend()}
              placeholder="Send instruction to agent..."
              className="flex-1 px-4 py-2.5 text-sm border border-border rounded-2xl bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
              disabled={sending}
            />
            <button
              onClick={handleSend}
              disabled={sending || !inputMsg.trim()}
              className="p-2.5 rounded-2xl bg-neutral-800 text-white hover:bg-neutral-700 disabled:opacity-50 transition-colors"
            >
              <PaperPlaneRight className="h-4 w-4" weight="fill" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
