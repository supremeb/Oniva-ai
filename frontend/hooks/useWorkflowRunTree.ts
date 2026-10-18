/**
 * useWorkflowRunTree — one workflow run as a `RunNode` tree (plan 003, U26).
 *
 * Active engine runs follow the container conversation's watch (`view.tasks`); finished runs, and runs the
 * engine view has not reached, read the stored trace. Waiting nodes come from the waiting-nodes route and are
 * decided through the engine decision route (Phase 5).
 */
import { useCallback, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { POLL_ACTIVE_MS, POLL_PARKED_MS, useWaitingNodes, useWorkflowLogDetail } from '@/hooks/useWorkflowLogs';
import { fetchWorkflowLogs, type WaitingNode, type WorkflowLogDetail } from '@/lib/api/workflow-logs';
import { EngineConversationClient } from '@/lib/engine/client';
import { DecisionError, toDecisionError } from '@/lib/engine/approval';
import { useEngineConversation } from '@/lib/engine/useEngineConversation';
import type { Connection } from '@/lib/engine/watcher';
import { createWorkflowViewAdapter, selectWorkflowTree } from '@/lib/run/adapters/workflowRun';
import { isActive, normalizeStatus } from '@/lib/run/status';
import type { RunNode } from '@/lib/run/tree';

const NO_WAITING: readonly WaitingNode[] = [];

/**
 * Waiting nodes of one execution and the decision call for them. `enabled` should be true only while the run is
 * active on the engine (legacy runs have no ledger rows to wait on).
 */
export function useWaitingDecisions(executionId: string | null, enabled: boolean, pollMs?: number) {
  const query = useWaitingNodes(executionId, enabled, pollMs);
  const client = useMemo(() => new EngineConversationClient(), []);
  const { refetch } = query;
  const decide = useCallback(
    async (node: WaitingNode, decision: 'approve' | 'deny', after?: () => Promise<unknown>) => {
      if (!node.conversationId || !node.taskId) {
        throw new DecisionError('This request cannot be decided from here yet. Ask an admin.', false);
      }
      try {
        await client.decide(node.conversationId, node.taskId, decision);
      } catch (e) {
        throw toDecisionError(e);
      }
      await Promise.all([refetch(), after?.()]);
    },
    [client, refetch]
  );
  return { waiting: query.data ?? NO_WAITING, decide };
}

/**
 * The execution a test run just started, found through the logs list (the stream only names its execution on
 * completion), with its waiting nodes. Only looks while `running`. Only manual runs qualify, so a scheduled or API
 * run of the same workflow is never picked up; another manual run started within the window still could be until
 * execute-stream reports `execution_id` in its first event (needs a backend change).
 */
export function useTestRunWaiting(workflowId: string | null, running: boolean, sinceMs: number) {
  const valid = !!workflowId && !workflowId.startsWith('local-');
  const latest = useQuery({
    queryKey: ['test-run-latest', workflowId],
    queryFn: () => fetchWorkflowLogs(workflowId!, { limit: 1, trigger: 'manual' }),
    enabled: running && valid,
    staleTime: 0,
    retry: false,
    refetchInterval: 2_000,
  });
  const log = latest.data?.logs[0];
  const fresh =
    running && log?.trigger === 'manual' && !!log.engine_conversation_id && Date.parse(log.started_at ?? '') >= sinceMs - 5_000 && isActive(normalizeStatus(log.status, 'workflow_log'));
  return useWaitingDecisions(fresh && log ? log.id : null, fresh);
}

export interface WorkflowRunTreeState {
  log: WorkflowLogDetail | null;
  tree: RunNode[];
  /** Waiting-node info by `RunNode.id`. */
  waiting: Map<string, WaitingNode>;
  source: 'engine' | 'trace' | null;
  connection: Connection | null;
  isLoading: boolean;
  error: unknown;
  /** Approve or deny a waiting node. Rejects with a `DecisionError` carrying the copy to show. */
  decide: (node: WaitingNode, decision: 'approve' | 'deny') => Promise<void>;
}

export function useWorkflowRunTree(workflowId: string | null, runId: string | null): WorkflowRunTreeState {
  const detail = useWorkflowLogDetail(workflowId, runId);
  const log = detail.data ?? null;
  const status = log ? normalizeStatus(log.status, 'workflow_log') : null;
  const active = !!status && isActive(status);
  const conversationId = active ? (log?.engine_conversation_id ?? null) : null;
  const conv = useEngineConversation(conversationId);
  const { waiting: waitingList, decide: decideNode } = useWaitingDecisions(runId, conversationId !== null, status === 'waiting' ? POLL_PARKED_MS : POLL_ACTIVE_MS);
  const adapt = useMemo(() => createWorkflowViewAdapter(), [runId]);

  const selected = useMemo(
    () => (log ? selectWorkflowTree({ log, view: conversationId ? conv.view : null, waiting: waitingList, adapt }) : null),
    [log, conv.view, conversationId, waitingList, adapt]
  );

  const { refetch: refetchDetail } = detail;
  const decide = useCallback(
    (node: WaitingNode, decision: 'approve' | 'deny') => decideNode(node, decision, refetchDetail),
    [decideNode, refetchDetail]
  );

  return {
    log,
    tree: selected?.tree ?? [],
    waiting: selected?.waiting ?? new Map(),
    source: selected?.source ?? null,
    connection: conversationId ? conv.connection : null,
    isLoading: detail.isLoading,
    error: detail.error,
    decide,
  };
}
