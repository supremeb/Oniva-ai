'use client';

import { useMemo } from 'react';
import { useWorkerTranscript } from '@/lib/engine/useEngineDeepWork';
import { viewToMessages } from '@/lib/engine/viewToMessages';

interface WorkerTranscriptProps {
  sessionId: string;
  childConversationId: string;
}

/**
 * Read-only transcript of one worker's child conversation. Mount it only while
 * the row is expanded: mounting opens one child watch, unmounting closes it.
 */
export function WorkerTranscript({ sessionId, childConversationId }: WorkerTranscriptProps) {
  const { view, connection, error } = useWorkerTranscript(sessionId, childConversationId);
  const messages = useMemo(() => (view ? viewToMessages(view) : []), [view]);

  if (error) {
    return (
      <p role="alert" className="mt-1.5 text-xs text-red-500">
        {error === 'forbidden' ? 'You do not have access to this worker.' : 'This worker transcript is not available.'}
      </p>
    );
  }
  if (!view) {
    return <p className="mt-1.5 text-xs italic text-neutral-400">Loading transcript...</p>;
  }
  return (
    <div data-testid="worker-transcript" data-connection={connection} className="mt-2 space-y-2" aria-label="Worker transcript">
      {messages.length === 0 && <p className="text-xs italic text-neutral-400">No activity yet.</p>}
      {messages.map((m) => (
        <div key={m.id} className="text-xs leading-relaxed">
          <span className="font-medium text-neutral-500">{m.role === 'user' ? 'Task' : 'Worker'}</span>
          <p className="whitespace-pre-wrap text-neutral-700">{m.content}</p>
        </div>
      ))}
    </div>
  );
}
