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
import { PaperPlaneRight, Robot, Exam, Link, CheckCircle, XCircle, Stop } from '@phosphor-icons/react';
import { interveneInRun } from '@/lib/api/fleet';
import { cancelJob } from '@/lib/api/background-jobs';
import { RunTree } from '@/components/run/RunTree';
import { RunStatusBadge } from '@/components/run/RunStatusBadge';
import { QueuedChips, type SendingChip } from '@/components/chat/QueuedChips';
import { isActive } from '@/lib/run/status';
import { ChatMessage, type ChatMessageData } from '@/components/chat/shared/ChatMessage';
import { ChainOfThought } from '@/components/chat/shared/ChainOfThought';
import { useFleetJobStream } from '@/hooks/useFleetJobStream';
import { cn } from '@/lib/utils';

interface FleetChatProps {
  runId: string;
  agentName: string;
  status: string;
  /** Engine conversation of an engine job; null or absent for a legacy job (trace path). */
  conversationId?: string | null;
}

export function FleetChat({ runId, agentName, conversationId = null }: FleetChatProps) {
  const { parts, toolCallMeta, messages, tree, inbox, engine, connection, status, job, isLive, loading, error, refresh, cancelSubmission } =
    useFleetJobStream(runId, { conversationId });

  const [interventions, setInterventions] = useState<ChatMessageData[]>([]);
  const [inputMsg, setInputMsg] = useState('');
  const [sending, setSending] = useState(false);
  const [sendingText, setSendingText] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
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
    setSendingText(text);

    try {
      await interveneInRun(runId, text);
      // Pessimistic: only show message after confirmed delivery
      // Engine jobs show the steer from the server (queued chip, then the user entry); legacy jobs echo locally.
      if (!engine) {
        const userMsg: ChatMessageData = {
          id: `user-${Date.now()}`,
          role: 'user',
          content: text,
        };
        setInterventions((prev) => [...prev, userMsg]);
      }
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
    setSendingText(null);
  };

  const handleCancel = async () => {
    setCancelling(true);
    setCancelError(null);
    try {
      await cancelJob(runId, 'Cancelled from Fleet');
      await refresh();
    } catch {
      setCancelError('Failed to cancel the run.');
    }
    setCancelling(false);
  };

  const active = isActive(status);
  const isRunning = active;
  const canCancel = status === 'running' || status === 'waiting' || status === 'queued' || status === 'resuming';
  const sendingChips: SendingChip[] = sendingText ? [{ requestId: 'local', mode: 'steer', text: sendingText }] : [];

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
        Loading...
      </div>
    );
  }

  const result = job?.result as { score?: number; passed?: boolean; action?: string } | null | undefined;

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border flex items-center gap-2 shrink-0">
        <Robot className="h-4 w-4 text-primary" weight="fill" />
        <span className="text-sm font-semibold">{job?.agent_name || agentName}</span>
        <RunStatusBadge status={status} testId="fleet-detail-status" />
        {isLive && (
          <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" title="Live" />
        )}
        {job?.duration_ms && (
          <span className="text-xs text-muted-foreground ml-auto">
            {(job.duration_ms / 1000).toFixed(0)}s
          </span>
        )}
        {canCancel && (
          <button
            type="button"
            onClick={handleCancel}
            disabled={cancelling}
            aria-label="Cancel run"
            className={cn('flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50', !job?.duration_ms && 'ml-auto')}
          >
            <Stop className="h-3 w-3" weight="fill" />
            Cancel
          </button>
        )}
      </div>

      {engine && (connection === 'reconnecting' || connection === 'stalled') && (
        <div data-testid="connection-banner" data-connection={connection} role="status" className="px-4 py-1.5 text-xs bg-amber-50 text-amber-800 border-b border-border">
          Reconnecting to the run...
        </div>
      )}
      {status === 'resuming' && (
        <div role="status" className="px-4 py-1.5 text-xs bg-blue-50 text-blue-800 border-b border-border">
          The worker restarted; picking the run back up where it left off.
        </div>
      )}
      {cancelError && (
        <div role="alert" className="px-4 py-1.5 text-xs bg-destructive/10 text-destructive border-b border-border">
          {cancelError}
        </div>
      )}
      {engine && tree.length > 0 && (
        <div className="px-4 py-2 border-b border-border max-h-56 overflow-y-auto shrink-0">
          <RunTree nodes={tree} aria-label="Run activity" />
        </div>
      )}

      {/* Channel status — evaluator score + webhook delivery */}
      {!isRunning && result && (result.score != null || result.action) && (
        <div className="px-4 py-2 border-b border-border flex items-center gap-4 text-xs text-muted-foreground">
          {result.score != null && (
            <span className={cn(
              'flex items-center gap-1',
              result.passed ? 'text-green-600' : 'text-amber-600'
            )}>
              <Exam className="h-3.5 w-3.5" />
              Score: {result.score}/100
              {result.passed ? ' (passed)' : ' (review)'}
            </span>
          )}
          {result.action === 'delivered' && (
            <span className="flex items-center gap-1 text-green-600">
              <Link className="h-3.5 w-3.5" />
              Webhook delivered
            </span>
          )}
          {result.action === 'delivered_without_evaluation' && (
            <span className="flex items-center gap-1">
              <CheckCircle className="h-3.5 w-3.5" />
              Delivered (no evaluation)
            </span>
          )}
          {result.action === 'routed_to_review' && (
            <span className="flex items-center gap-1 text-amber-600">
              <XCircle className="h-3.5 w-3.5" />
              Sent to review queue
            </span>
          )}
        </div>
      )}

      {/* Messages — agent activity + operator interventions */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4">
        {((engine ? messages.length === 0 : parts.length === 0) && interventions.length === 0) ? (
          <div className="text-sm text-muted-foreground text-center py-8">
            {isRunning ? 'Waiting for agent to start...' : 'No execution trace available.'}
          </div>
        ) : (
          <div className="space-y-4">
            {engine ? (
              /* Engine job: the watched conversation, in order (steers are user entries). */
              messages.map((m) =>
                m.role === 'user' ? (
                  <ChatMessage
                    key={m.id}
                    message={{ id: m.id, role: 'user', content: m.content }}
                    renderMarkdown={false}
                    showAvatar={false}
                    compact
                  />
                ) : m.richParts && m.richParts.length > 0 ? (
                  <div key={m.id} className="flex gap-3 justify-start">
                    <div className="flex flex-col w-full min-w-0">
                      <ChainOfThought parts={m.richParts} compact animated={isLive && m.isStreaming} toolCallMeta={toolCallMeta} />
                    </div>
                  </div>
                ) : null
              )
            ) : (
              /* Legacy job: agent activity rendered as ChainOfThought from the trace */
              parts.length > 0 && (
                <div className="flex gap-3 justify-start">
                  <div className="flex flex-col w-full min-w-0">
                    <ChainOfThought parts={parts} compact animated={isLive} toolCallMeta={toolCallMeta} />
                  </div>
                </div>
              )
            )}

            {/* Error banner */}
            {error && (
              <div className="px-3 py-2 rounded-lg bg-destructive/10 text-destructive text-sm">
                {error}
              </div>
            )}

            {/* Operator intervention messages (legacy echo and send failures) */}
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

      {/* Input — live for active jobs; engine jobs keep it visible but disabled once the run has ended */}
      {(isRunning || engine) && (
        <div className="border-t border-border px-4 py-3 shrink-0">
          {engine && (
            <div className="max-w-3xl mx-auto">
              <QueuedChips inbox={inbox} sending={sendingChips} onCancel={cancelSubmission} />
            </div>
          )}
          <div className="flex items-center gap-2 max-w-3xl mx-auto">
            <input
              type="text"
              aria-label="Message to agent"
              value={inputMsg}
              onChange={(e) => setInputMsg(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend()}
              placeholder={isRunning ? 'Send instruction to agent...' : 'This run has ended'}
              className="flex-1 px-4 py-2.5 text-sm border border-border rounded-2xl bg-background focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-60"
              disabled={sending || !isRunning}
            />
            <button
              onClick={handleSend}
              aria-label="Send"
              disabled={sending || !isRunning || !inputMsg.trim()}
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
