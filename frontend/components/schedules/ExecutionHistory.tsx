'use client';

import { useState } from 'react';
import { formatDistanceToNow, format } from 'date-fns';
import {
  CircleNotch,
  CheckCircle,
  XCircle,
  Clock,
  MinusCircle,
  CaretDown,
  Warning,
} from '@phosphor-icons/react';
import { useScheduleExecutions } from '@/hooks/useSchedules';
import type { ScheduleExecutionResponse } from '@/lib/api/schedules';

const STATUS_CONFIG: Record<
  string,
  { icon: React.ElementType; className: string; label: string; weight?: 'fill' | 'regular' }
> = {
  pending: {
    icon: Clock,
    className: 'text-muted-foreground bg-muted/60',
    label: 'Pending',
  },
  running: {
    icon: CircleNotch,
    className: 'text-blue-600 bg-blue-100 dark:text-blue-400 dark:bg-blue-950',
    label: 'Running',
  },
  completed: {
    icon: CheckCircle,
    className: 'text-green-600 bg-green-100 dark:text-green-400 dark:bg-green-950',
    label: 'Completed',
    weight: 'fill',
  },
  failed: {
    icon: XCircle,
    className: 'text-red-600 bg-red-100 dark:text-red-400 dark:bg-red-950',
    label: 'Failed',
    weight: 'fill',
  },
  skipped: {
    icon: MinusCircle,
    className: 'text-yellow-600 bg-yellow-100 dark:text-yellow-400 dark:bg-yellow-950',
    label: 'Skipped',
  },
};

function StatusBadge({ status }: { status: string }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
  const Icon = config.icon;

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-medium rounded-md ${config.className}`}
    >
      <Icon
        className={`h-3.5 w-3.5 ${status === 'running' ? 'animate-spin' : ''}`}
        weight={config.weight || 'regular'}
      />
      {config.label}
    </span>
  );
}

function formatDuration(startedAt: string | null, completedAt: string | null): string {
  if (!startedAt) return '-';
  const start = new Date(startedAt).getTime();
  const end = completedAt ? new Date(completedAt).getTime() : Date.now();
  const seconds = Math.floor((end - start) / 1000);

  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}

/** Extract the LLM/final text output from a node result object. */
function extractNodeText(output: unknown): string | null {
  if (!output || typeof output !== 'object') return null;
  const o = output as Record<string, unknown>;
  // LLM nodes store text in output.text
  if (typeof o.text === 'string' && o.text) return o.text;
  // Skill nodes store results in output.result
  if (o.result && typeof o.result === 'object') {
    const r = o.result as Record<string, unknown>;
    if (Array.isArray(r.results) && r.results.length > 0) {
      return r.results
        .map((item: Record<string, unknown>) =>
          `- [${item.title || 'Untitled'}](${item.url || ''})\n  ${(item.snippet as string || '').slice(0, 200)}`
        )
        .join('\n');
    }
  }
  // Output node
  if (o.final === true && o.output && typeof o.output === 'object') {
    const finalOutput = o.output as Record<string, unknown>;
    const texts = Object.values(finalOutput).filter((v) => typeof v === 'string' && v);
    if (texts.length > 0) return texts.join('\n\n');
  }
  return null;
}

function NodeResultCard({ nodeId, nodeResult }: { nodeId: string; nodeResult: Record<string, unknown> }) {
  const status = nodeResult.status as string;
  const error = nodeResult.error as string | null;
  const output = nodeResult.output;
  const tokens = nodeResult.tokens_used as number | null;
  const elapsedMs = nodeResult.elapsed_ms as number | null;
  const label = nodeId.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

  const text = extractNodeText(output);
  const isStart = nodeId.startsWith('start');

  if (isStart) return null;

  return (
    <div className="rounded-lg border bg-card">
      <div className="flex items-center justify-between px-3 py-2 border-b bg-muted/20">
        <div className="flex items-center gap-2">
          <StatusBadge status={status} />
          <span className="text-xs font-medium">{label}</span>
        </div>
        <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
          {tokens != null && <span>{tokens.toLocaleString()} tokens</span>}
          {elapsedMs != null && elapsedMs > 0 && <span>{(elapsedMs / 1000).toFixed(1)}s</span>}
        </div>
      </div>

      {error && (
        <div className="px-3 py-2 bg-red-50 dark:bg-red-950/30 border-b">
          <div className="flex items-center gap-1.5 text-xs font-medium text-red-700 dark:text-red-400">
            <Warning className="h-3.5 w-3.5" />
            {error}
          </div>
        </div>
      )}

      {text && (
        <div className="max-h-48 overflow-y-auto">
          <pre className="px-3 py-2 text-xs whitespace-pre-wrap break-words leading-relaxed">
            {text}
          </pre>
        </div>
      )}
    </div>
  );
}

function AccordionRow({ exec }: { exec: ScheduleExecutionResponse }) {
  const [expanded, setExpanded] = useState(false);

  const results = (exec.result_summary as Record<string, unknown>)?.results as
    | Record<string, Record<string, unknown>>
    | undefined;

  const nodeIds = results ? Object.keys(results) : [];

  return (
    <>
      <tr
        className="hover:bg-muted/20 transition-colors cursor-pointer group"
        onClick={() => setExpanded((prev) => !prev)}
      >
        <td className="px-3 py-2 text-xs">
          {format(new Date(exec.scheduled_for), 'MMM d, HH:mm')}
        </td>
        <td className="px-3 py-2 text-xs text-muted-foreground">
          {exec.started_at
            ? formatDistanceToNow(new Date(exec.started_at), { addSuffix: true })
            : '-'}
        </td>
        <td className="px-3 py-2 text-xs font-mono">
          {formatDuration(exec.started_at, exec.completed_at)}
        </td>
        <td className="px-3 py-2">
          <StatusBadge status={exec.status} />
        </td>
        <td className="px-3 py-2 text-xs text-muted-foreground">
          {exec.retry_count > 0 ? exec.retry_count : '-'}
        </td>
        <td className="px-3 py-2">
          <CaretDown
            className={`h-4 w-4 text-muted-foreground transition-transform duration-200 ${
              expanded ? 'rotate-180' : ''
            }`}
          />
        </td>
      </tr>

      {expanded && (
        <tr>
          <td colSpan={6} className="p-0">
            <div className="border-t bg-muted/10 px-4 py-3 space-y-3">
              {/* Info row */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                <div>
                  <span className="text-muted-foreground">Scheduled</span>
                  <p className="font-medium">
                    {format(new Date(exec.scheduled_for), 'MMM d, yyyy HH:mm:ss')}
                  </p>
                </div>
                <div>
                  <span className="text-muted-foreground">Started</span>
                  <p className="font-medium">
                    {exec.started_at
                      ? format(new Date(exec.started_at), 'HH:mm:ss')
                      : '-'}
                  </p>
                </div>
                <div>
                  <span className="text-muted-foreground">Completed</span>
                  <p className="font-medium">
                    {exec.completed_at
                      ? format(new Date(exec.completed_at), 'HH:mm:ss')
                      : '-'}
                  </p>
                </div>
                <div>
                  <span className="text-muted-foreground">Duration</span>
                  <p className="font-medium font-mono">
                    {formatDuration(exec.started_at, exec.completed_at)}
                  </p>
                </div>
              </div>

              {/* Error banner */}
              {exec.error && (
                <div className="rounded-lg border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 p-3">
                  <div className="flex items-center gap-1.5 text-xs font-medium text-red-700 dark:text-red-400 mb-1">
                    <Warning className="h-3.5 w-3.5" />
                    Error
                  </div>
                  <pre className="text-xs text-red-600 dark:text-red-300 whitespace-pre-wrap break-words max-h-24 overflow-y-auto">
                    {exec.error}
                  </pre>
                </div>
              )}

              {/* Node outputs */}
              {nodeIds.length > 0 && (
                <div className="space-y-2">
                  <span className="text-xs font-medium text-muted-foreground">Node Outputs</span>
                  <div className="max-h-[400px] overflow-y-auto space-y-2 pr-1">
                    {nodeIds.map((nodeId) => (
                      <NodeResultCard
                        key={nodeId}
                        nodeId={nodeId}
                        nodeResult={results![nodeId]}
                      />
                    ))}
                  </div>
                </div>
              )}

              {/* Fallback: raw summary when no structured results */}
              {!results && exec.result_summary && Object.keys(exec.result_summary).length > 0 && (
                <div>
                  <span className="text-xs font-medium text-muted-foreground">Result Summary</span>
                  <pre className="mt-1 text-xs bg-muted/50 rounded-lg p-3 overflow-auto max-h-48">
                    {JSON.stringify(exec.result_summary, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

interface ExecutionHistoryProps {
  scheduleId: string;
}

export function ExecutionHistory({ scheduleId }: ExecutionHistoryProps) {
  const { data, isLoading } = useScheduleExecutions(scheduleId);
  const executions = data?.items || [];

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <CircleNotch className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (executions.length === 0) {
    return (
      <div className="text-center py-6 text-sm text-muted-foreground">
        No executions yet. The schedule will run at its next scheduled time.
      </div>
    );
  }

  return (
    <div>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/30">
              <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">
                Scheduled For
              </th>
              <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">
                Started
              </th>
              <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">
                Duration
              </th>
              <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">
                Status
              </th>
              <th className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">
                Retries
              </th>
              <th className="px-3 py-2 w-8" />
            </tr>
          </thead>
          <tbody className="divide-y divide-border/50">
            {executions.map((exec) => (
              <AccordionRow key={exec.id} exec={exec} />
            ))}
          </tbody>
        </table>
      </div>

      {data && data.total > executions.length && (
        <p className="text-xs text-muted-foreground text-center mt-2">
          Showing {executions.length} of {data.total} executions
        </p>
      )}
    </div>
  );
}
