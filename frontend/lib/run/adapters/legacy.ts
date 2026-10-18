/** Legacy Fleet job and workflow log rows -> RunNode (plan 003, U23). Pure; memoized per input object. */
import type { BackgroundJob } from '@/lib/types/chat-panels';
import type { WorkflowLogDetail, WorkflowLogSummary } from '@/lib/api/workflow-logs';
import { normalizeStatus } from '../status';
import type { RunNode } from '../tree';

type ExecutionTraceStep = BackgroundJob['execution_trace'][number] | WorkflowLogDetail['execution_trace'][number];

const cache = new WeakMap<object, RunNode>();
const memo = <T extends object>(row: T, build: () => RunNode): RunNode => {
  const hit = cache.get(row);
  if (hit) return hit;
  const node = build();
  cache.set(row, node);
  return node;
};

function stepNode(runId: string, s: ExecutionTraceStep, i: number): RunNode {
  const node: RunNode = {
    id: `${runId}:${s.step_id ?? s.node_id ?? i}`,
    kind: 'node',
    label: s.node_type ? `${s.node_type}${s.node_id ? ` ${s.node_id}` : ''}` : s.node_id ?? s.step_id ?? `Step ${i + 1}`,
    status: normalizeStatus(s.status, 'execution_trace'),
    startedAt: s.started_at ?? null,
    endedAt: s.completed_at ?? null,
    children: [],
  };
  if (s.error) node.error = s.error;
  return node;
}

const steps = (id: string, trace: ExecutionTraceStep[] | undefined): RunNode[] =>
  Array.isArray(trace) ? trace.map((s, i) => stepNode(id, s, i)) : [];

export function backgroundJobToRunNode(job: BackgroundJob): RunNode {
  return memo(job, () => {
    const node: RunNode = {
      id: job.id,
      kind: 'run',
      label: job.workflow_name ?? job.agent_name ?? job.job_type,
      status: normalizeStatus(job.status, 'background_job'),
      startedAt: job.started_at ?? job.created_at ?? null,
      endedAt: job.completed_at ?? null,
      children: steps(job.id, job.execution_trace),
    };
    if (job.error) node.error = job.error;
    return node;
  });
}

export function workflowLogToRunNode(log: WorkflowLogSummary | WorkflowLogDetail): RunNode {
  return memo(log, () => {
    const node: RunNode = {
      id: log.id,
      kind: 'run',
      label: log.version_number != null ? `Workflow v${log.version_number}` : 'Workflow run',
      status: normalizeStatus(log.status, 'workflow_log'),
      startedAt: log.started_at ?? null,
      endedAt: log.completed_at ?? null,
      children: steps(log.id, 'execution_trace' in log ? log.execution_trace : undefined),
    };
    if (typeof log.total_tokens === 'number') node.usage = { totalTokens: log.total_tokens };
    if (log.error) node.error = log.error;
    return node;
  });
}
