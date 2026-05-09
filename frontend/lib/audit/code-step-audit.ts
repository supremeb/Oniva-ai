/**
 * Code Step Audit Logger
 *
 * Logs code step changes for security audit trail.
 * This sends audit events to the backend for permanent storage.
 */

// ============ Types ============

export interface CodeStepAuditEvent {
  eventType: 'code_step_created' | 'code_step_modified' | 'code_step_deleted';
  timestamp: string;
  workflowId: string | null;
  stepId: string;
  language: 'python' | 'javascript' | null;
  previousCode: string | null;
  newCode: string | null;
  codeLength: number | null;
  timeout: number | null;
}

// ============ Audit Queue ============

/**
 * Queue of audit events waiting to be sent to backend.
 * Batched for efficiency.
 */
const auditQueue: CodeStepAuditEvent[] = [];
let flushTimeout: ReturnType<typeof setTimeout> | null = null;

const FLUSH_DELAY_MS = 1000;  // Batch events for 1 second
const MAX_BATCH_SIZE = 10;

/**
 * Flush the audit queue to the backend.
 */
async function flushAuditQueue(): Promise<void> {
  if (auditQueue.length === 0) return;

  const events = auditQueue.splice(0, MAX_BATCH_SIZE);

  try {
    // In development, just log to console
    if (process.env.NODE_ENV === 'development') {
      console.log('[Audit] Code step changes:', events);
      return;
    }

    // In production, send to backend audit endpoint
    const response = await fetch('/api/v1/audit/code-step', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ events }),
    });

    if (!response.ok) {
      // Re-queue failed events for retry
      console.warn('[Audit] Failed to send audit events, re-queuing');
      auditQueue.unshift(...events);
    }
  } catch (error) {
    // Re-queue on network error
    console.error('[Audit] Error sending audit events:', error);
    auditQueue.unshift(...events);
  }
}

/**
 * Schedule a queue flush.
 */
function scheduleFlush(): void {
  if (flushTimeout) return;

  flushTimeout = setTimeout(() => {
    flushTimeout = null;
    flushAuditQueue();
  }, FLUSH_DELAY_MS);
}

// ============ Public API ============

/**
 * Log a code step change for audit.
 */
export function logCodeStepChange(params: {
  stepId: string;
  workflowId: string | null;
  eventType: 'code_step_created' | 'code_step_modified' | 'code_step_deleted';
  previousCode?: string | null;
  newCode?: string | null;
  language?: 'python' | 'javascript' | null;
  timeout?: number | null;
}): void {
  const event: CodeStepAuditEvent = {
    eventType: params.eventType,
    timestamp: new Date().toISOString(),
    workflowId: params.workflowId,
    stepId: params.stepId,
    language: params.language ?? null,
    previousCode: params.previousCode ?? null,
    newCode: params.newCode ?? null,
    codeLength: params.newCode?.length ?? null,
    timeout: params.timeout ?? null,
  };

  auditQueue.push(event);
  scheduleFlush();
}

/**
 * Helper to determine if a config change involves code steps.
 * Returns the appropriate event type.
 */
export function getCodeStepEventType(
  previousConfig: { kind: string } | undefined,
  newConfig: { kind: string }
): 'code_step_created' | 'code_step_modified' | 'code_step_deleted' | null {
  const wasCode = previousConfig?.kind === 'code';
  const isCode = newConfig.kind === 'code';

  if (!wasCode && isCode) {
    return 'code_step_created';
  } else if (wasCode && !isCode) {
    return 'code_step_deleted';
  } else if (wasCode && isCode) {
    return 'code_step_modified';
  }

  return null;  // Not a code step change
}

/**
 * Force flush any pending audit events.
 * Call this before page unload.
 */
export function flushPendingAuditEvents(): void {
  if (flushTimeout) {
    clearTimeout(flushTimeout);
    flushTimeout = null;
  }
  flushAuditQueue();
}

// Register for page unload
if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', flushPendingAuditEvents);
}
