'use client';

import { Fragment, useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  CircleNotch,
  Warning,
  CaretDown,
  CaretRight,
  CaretLeft,
  MagnifyingGlass,
  CheckCircle,
  XCircle,
  Clock,
  SkipForward,
  Brain,
  Lightning,
} from '@phosphor-icons/react';
import { StatusBadge } from '@/components/chat/shared/data/StatusBadge';
import { useTraceSearch } from '@/hooks/useObservability';
import { format } from 'date-fns';
import type { StatusType } from '@/components/chat/shared/data/StatusBadge';
import { cn } from '@/lib/utils';

const STATUS_OPTIONS = [
  { label: 'All', value: undefined },
  { label: 'Completed', value: 'completed' },
  { label: 'Running', value: 'running' },
  { label: 'Failed', value: 'failed' },
  { label: 'Pending', value: 'pending' },
];

const PAGE_SIZE = 20;

type ViewTab = 'executions' | 'llm_calls';

function traceStatusToBadge(status: string): StatusType {
  switch (status) {
    case 'completed': return 'success';
    case 'failed': return 'error';
    case 'running': return 'running';
    case 'pending': return 'pending';
    case 'cancelled': return 'cancelled';
    default: return 'neutral';
  }
}

function formatDuration(ms: number | null): string {
  if (ms === null) return '--';
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)}m`;
  if (ms >= 1_000) return `${(ms / 1_000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}

function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toString();
}

function formatCost(usd: number): string {
  if (usd >= 1) return `$${usd.toFixed(2)}`;
  if (usd >= 0.01) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(4)}`;
}

const SOURCE_LABELS: Record<string, string> = {
  background_agent: 'Agent Run',
  deep_work: 'Deep Work',
  workflow: 'Workflow',
  chat: 'Chat',
  workflow_execution: 'Workflow',
  agent_chat: 'Chat',
};

const STEP_STATUS_ICONS: Record<string, typeof CheckCircle> = {
  pending: Clock,
  running: CircleNotch,
  completed: CheckCircle,
  failed: XCircle,
  skipped: SkipForward,
};

const STEP_STATUS_COLORS: Record<string, string> = {
  pending: 'text-muted-foreground',
  running: 'text-blue-500',
  completed: 'text-green-500',
  failed: 'text-destructive',
  skipped: 'text-muted-foreground',
};

export function TracingTab() {
  const [viewTab, setViewTab] = useState<ViewTab>('executions');
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined);
  const [offset, setOffset] = useState(0);
  const [expandedTraces, setExpandedTraces] = useState<Set<string>>(new Set());
  const [expandedCalls, setExpandedCalls] = useState<Set<string>>(new Set());
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());

  const toggleTrace = (id: string) => {
    setExpandedTraces((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleCall = (id: string) => {
    setExpandedCalls((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleStep = (stepKey: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepKey)) next.delete(stepKey); else next.add(stepKey);
      return next;
    });
  };

  const { data, isLoading, error } = useTraceSearch({
    status: viewTab === 'executions' ? statusFilter : undefined,
    offset,
    limit: PAGE_SIZE,
  });

  const traces = data?.traces ?? [];
  const llmCalls = data?.llm_calls ?? [];
  const canGoBack = offset > 0;
  const canGoForward = viewTab === 'executions'
    ? traces.length >= PAGE_SIZE
    : llmCalls.length >= PAGE_SIZE;

  return (
    <div className="space-y-4">
      {/* View tabs + Filters */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="flex rounded-lg border border-border overflow-hidden">
            <button
              onClick={() => { setViewTab('executions'); setOffset(0); }}
              className={cn(
                'px-3 py-1.5 text-xs font-medium transition-colors',
                viewTab === 'executions'
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-background text-muted-foreground hover:text-foreground'
              )}
            >
              Executions
            </button>
            <button
              onClick={() => { setViewTab('llm_calls'); setOffset(0); }}
              className={cn(
                'px-3 py-1.5 text-xs font-medium transition-colors border-l border-border',
                viewTab === 'llm_calls'
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-background text-muted-foreground hover:text-foreground'
              )}
            >
              LLM Calls
            </button>
          </div>

          {viewTab === 'executions' && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="gap-2">
                  Status: {STATUS_OPTIONS.find((o) => o.value === statusFilter)?.label || 'All'}
                  <CaretDown className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                {STATUS_OPTIONS.map((opt) => (
                  <DropdownMenuItem
                    key={opt.label}
                    onClick={() => { setStatusFilter(opt.value); setOffset(0); }}
                  >
                    {opt.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>

        {data && (
          <p className="text-xs text-muted-foreground">
            {data.total} result{data.total !== 1 ? 's' : ''}
          </p>
        )}
      </div>

      {/* Error State */}
      {error && (
        <div className="text-center py-12 text-muted-foreground">
          <Warning className="h-8 w-8 mx-auto mb-2" />
          <p className="text-sm">Failed to load traces</p>
        </div>
      )}

      {/* Loading State */}
      {isLoading && (
        <div className="flex justify-center py-12">
          <CircleNotch className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      )}

      {/* Executions Table */}
      {!isLoading && !error && data && viewTab === 'executions' && (
        <Card>
          <CardContent className="p-0">
            {traces.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground">
                <MagnifyingGlass className="h-8 w-8 mx-auto mb-2" />
                <p className="text-sm">No execution traces found</p>
                <p className="text-xs mt-1">
                  {statusFilter ? 'Try changing the status filter' : 'Run agents or workflows to see traces here'}
                </p>
              </div>
            ) : (
              <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8"></TableHead>
                    <TableHead className="whitespace-nowrap">Source</TableHead>
                    <TableHead className="whitespace-nowrap">Name</TableHead>
                    <TableHead className="whitespace-nowrap">Status</TableHead>
                    <TableHead className="text-right whitespace-nowrap">Duration</TableHead>
                    <TableHead className="text-right whitespace-nowrap">Steps</TableHead>
                    <TableHead className="whitespace-nowrap">Started</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {traces.map((trace) => {
                    const isExpanded = expandedTraces.has(trace.run_id);
                    return (
                      <Fragment key={trace.run_id}>
                        <TableRow className="cursor-pointer hover:bg-accent/50" onClick={() => toggleTrace(trace.run_id)}>
                          <TableCell className="w-8 px-2">
                            {isExpanded ? (
                              <CaretDown className="h-4 w-4 text-muted-foreground" />
                            ) : (
                              <CaretRight className="h-4 w-4 text-muted-foreground" />
                            )}
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline" className="text-[10px]">
                              {SOURCE_LABELS[trace.source] || trace.source}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <span className="font-medium text-sm">
                              {trace.agent_name || trace.workflow_name || (
                                <span className="text-muted-foreground font-mono text-xs">
                                  {trace.run_id.slice(0, 8)}
                                </span>
                              )}
                            </span>
                          </TableCell>
                          <TableCell>
                            <StatusBadge
                              status={traceStatusToBadge(trace.status)}
                              label={trace.status}
                              size="xs"
                            />
                          </TableCell>
                          <TableCell className="text-right font-mono text-xs">
                            {formatDuration(trace.duration_ms)}
                          </TableCell>
                          <TableCell className="text-right">
                            {trace.total_steps}
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {format(new Date(trace.created_at), 'MMM d, HH:mm')}
                          </TableCell>
                        </TableRow>
                        {isExpanded && (
                          <tr>
                            <td colSpan={7} className="p-0" style={{ maxWidth: 0 }}>
                              <div className="bg-muted/30 border-t px-4 py-3 space-y-3 overflow-hidden">
                                {/* Error */}
                                {trace.error && (
                                  <div>
                                    <p className="text-xs font-medium text-destructive mb-1">Error</p>
                                    <p className="text-xs bg-destructive/10 text-destructive p-2 rounded">
                                      {trace.error}
                                    </p>
                                  </div>
                                )}

                                {/* Input Data */}
                                {trace.input_data && Object.keys(trace.input_data).length > 0 && (
                                  <div>
                                    <p className="text-xs font-medium text-muted-foreground mb-1">Input</p>
                                    <pre className="text-xs bg-muted/50 p-2 rounded overflow-x-auto max-h-24 overflow-y-auto">
                                      {JSON.stringify(trace.input_data, null, 2)}
                                    </pre>
                                  </div>
                                )}

                                {/* Result */}
                                {trace.result && Object.keys(trace.result).length > 0 && (
                                  <div>
                                    <p className="text-xs font-medium text-muted-foreground mb-1">Result</p>
                                    <pre className="text-xs bg-muted/50 p-2 rounded overflow-x-auto max-h-24 overflow-y-auto">
                                      {JSON.stringify(trace.result, null, 2)}
                                    </pre>
                                  </div>
                                )}

                                {/* Execution Trace Steps */}
                                {trace.execution_trace && trace.execution_trace.length > 0 ? (
                                  <div>
                                    <div className="flex items-center gap-2 mb-2">
                                      <Lightning className="h-3.5 w-3.5 text-muted-foreground" />
                                      <p className="text-xs font-medium text-muted-foreground">
                                        Execution Steps ({trace.execution_trace.length})
                                      </p>
                                    </div>
                                    <div className="space-y-1.5">
                                      {trace.execution_trace.map((step, idx) => {
                                        // Detect format: agent executor has `type` field, workflow has `node_type`
                                        const isAgentStep = !!step.type;
                                        const stepStatus = step.status || (isAgentStep ? 'completed' : 'pending');
                                        const StepIcon = STEP_STATUS_ICONS[stepStatus] || Clock;
                                        const stepColor = STEP_STATUS_COLORS[stepStatus] || 'text-muted-foreground';
                                        const stepKey = `${trace.run_id}-${step.step_id || idx}`;
                                        const isStepExpanded = expandedSteps.has(stepKey);

                                        // Derive display label
                                        const stepLabel = isAgentStep
                                          ? (step.type === 'tool_call' ? (step.tool_name || 'Tool Call') : 'Response')
                                          : (step.node_name || step.node_type || 'Step');

                                        // Always expandable — every step has at least args/result/timestamps
                                        const hasDetails = isAgentStep
                                          ? !!(step.tool_args || step.tool_result_preview || step.content_preview || step.token_usage || step.timestamp)
                                          : !!(step.reasoning || step.error || (step.input && Object.keys(step.input).length > 0) || (step.output && Object.keys(step.output).length > 0) || step.started_at || step.completed_at || step.token_usage);

                                        return (
                                          <div key={step.step_id || idx} className={cn(
                                            'rounded-md border bg-background',
                                            stepStatus === 'failed' && 'border-destructive/40',
                                            stepStatus === 'completed' && 'border-green-500/20',
                                          )}>
                                            <button
                                              className={cn(
                                                'w-full flex items-center gap-2 p-2.5 text-left transition-colors rounded-md',
                                                hasDetails && 'hover:bg-accent/50 cursor-pointer',
                                              )}
                                              onClick={() => hasDetails && toggleStep(stepKey)}
                                            >
                                              {hasDetails ? (
                                                isStepExpanded
                                                  ? <CaretDown className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                                                  : <CaretRight className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                                              ) : (
                                                <span className="w-3" />
                                              )}
                                              <span className="text-xs text-muted-foreground w-5">{idx + 1}.</span>
                                              <StepIcon className={cn('h-3.5 w-3.5 flex-shrink-0', stepColor, stepStatus === 'running' && 'animate-spin')} />
                                              <span className="text-xs font-medium truncate">{stepLabel}</span>
                                              {isAgentStep && step.type === 'tool_call' && (
                                                <Badge variant="outline" className="text-[10px] font-mono">tool</Badge>
                                              )}
                                              {isAgentStep && step.type === 'text_response' && (
                                                <Badge variant="outline" className="text-[10px]">text</Badge>
                                              )}
                                              {!isAgentStep && step.node_id && (
                                                <Badge variant="outline" className="text-[10px] font-mono">{step.node_id}</Badge>
                                              )}
                                              {step.duration_ms != null && (
                                                <span className="text-[10px] text-muted-foreground ml-auto font-mono">
                                                  {formatDuration(step.duration_ms)}
                                                </span>
                                              )}
                                            </button>
                                            {isStepExpanded && (
                                              <div className="px-2.5 pb-2.5 pt-0 space-y-2 border-t mx-3">
                                                {/* Agent executor: tool call details */}
                                                {isAgentStep && step.type === 'tool_call' && (
                                                  <>
                                                    {step.tool_args && Object.keys(step.tool_args).length > 0 && (
                                                      <div className="mt-2">
                                                        <p className="text-[10px] text-muted-foreground mb-0.5">Arguments</p>
                                                        <pre className="text-[11px] bg-muted/50 p-1.5 rounded overflow-x-auto max-h-24 overflow-y-auto break-all whitespace-pre-wrap">
                                                          {JSON.stringify(step.tool_args, null, 2)}
                                                        </pre>
                                                      </div>
                                                    )}
                                                    {step.tool_result_preview && (
                                                      <div>
                                                        <p className="text-[10px] text-muted-foreground mb-0.5">Result</p>
                                                        <pre className="text-[11px] bg-muted/50 p-1.5 rounded overflow-x-auto max-h-32 overflow-y-auto break-all whitespace-pre-wrap">
                                                          {step.tool_result_preview}
                                                        </pre>
                                                      </div>
                                                    )}
                                                  </>
                                                )}
                                                {/* Agent executor: text response */}
                                                {isAgentStep && step.type === 'text_response' && step.content_preview && (
                                                  <div className="mt-2">
                                                    <p className="text-[10px] text-muted-foreground mb-0.5">Response</p>
                                                    <p className="text-[11px] bg-muted/50 p-1.5 rounded whitespace-pre-wrap break-words max-h-32 overflow-y-auto">
                                                      {step.content_preview}
                                                    </p>
                                                  </div>
                                                )}
                                                {/* Workflow: reasoning */}
                                                {!isAgentStep && step.reasoning && (
                                                  <div className="mt-2">
                                                    <div className="flex items-center gap-1 text-[10px] text-muted-foreground mb-0.5">
                                                      <Brain className="h-2.5 w-2.5" />
                                                      Reasoning
                                                    </div>
                                                    <p className="text-[11px] bg-muted/50 p-1.5 rounded">{step.reasoning}</p>
                                                  </div>
                                                )}
                                                {/* Workflow: input */}
                                                {!isAgentStep && step.input && Object.keys(step.input).length > 0 && (
                                                  <div>
                                                    <p className="text-[10px] text-muted-foreground mb-0.5">Input</p>
                                                    <pre className="text-[11px] bg-muted/50 p-1.5 rounded overflow-x-auto max-h-24 overflow-y-auto break-all whitespace-pre-wrap">
                                                      {JSON.stringify(step.input, null, 2)}
                                                    </pre>
                                                  </div>
                                                )}
                                                {/* Workflow: output */}
                                                {!isAgentStep && step.output && Object.keys(step.output).length > 0 && (
                                                  <div>
                                                    <p className="text-[10px] text-muted-foreground mb-0.5">Output</p>
                                                    <pre className="text-[11px] bg-muted/50 p-1.5 rounded overflow-x-auto max-h-24 overflow-y-auto break-all whitespace-pre-wrap">
                                                      {JSON.stringify(step.output, null, 2)}
                                                    </pre>
                                                  </div>
                                                )}
                                                {/* Error (both formats) */}
                                                {step.error && (
                                                  <div>
                                                    <p className="text-[10px] text-destructive mb-0.5">Error</p>
                                                    <p className="text-[11px] bg-destructive/10 text-destructive p-1.5 rounded">
                                                      {step.error}
                                                    </p>
                                                  </div>
                                                )}
                                                {/* Token usage (both formats) */}
                                                {step.token_usage && (
                                                  <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
                                                    <span>Tokens: {step.token_usage.total?.toLocaleString() ?? '--'}</span>
                                                    <span>In: {step.token_usage.input?.toLocaleString() ?? '--'}</span>
                                                    <span>Out: {step.token_usage.output?.toLocaleString() ?? '--'}</span>
                                                  </div>
                                                )}
                                                {/* Timestamps */}
                                                {(step.started_at || step.completed_at || step.timestamp) && (
                                                  <div className="flex items-center gap-4 text-[10px] text-muted-foreground pt-1">
                                                    {step.timestamp && <span>Time: {new Date(step.timestamp).toLocaleTimeString()}</span>}
                                                    {step.started_at && <span>Started: {new Date(step.started_at).toLocaleTimeString()}</span>}
                                                    {step.completed_at && <span>Completed: {new Date(step.completed_at).toLocaleTimeString()}</span>}
                                                  </div>
                                                )}
                                              </div>
                                            )}
                                          </div>
                                        );
                                      })}
                                    </div>
                                  </div>
                                ) : (
                                  <p className="text-xs text-muted-foreground">No execution steps recorded</p>
                                )}

                                {/* Metadata */}
                                <div className="flex items-center gap-4 text-[10px] text-muted-foreground pt-1 border-t">
                                  <span>Run ID: <span className="font-mono">{trace.run_id.slice(0, 12)}</span></span>
                                  {trace.workflow_id && <span>Workflow: <span className="font-mono">{trace.workflow_id.slice(0, 8)}</span></span>}
                                  {trace.completed_at && <span>Completed: {format(new Date(trace.completed_at), 'MMM d, HH:mm:ss')}</span>}
                                </div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </TableBody>
              </Table>
              </div>
            )}
          </CardContent>

          {traces.length > 0 && (
            <div className="flex items-center justify-end px-4 py-3 border-t gap-2">
              <Button variant="outline" size="sm" disabled={!canGoBack} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                <CaretLeft className="h-4 w-4" />
              </Button>
              <Button variant="outline" size="sm" disabled={!canGoForward} onClick={() => setOffset(offset + PAGE_SIZE)}>
                <CaretRight className="h-4 w-4" />
              </Button>
            </div>
          )}
        </Card>
      )}

      {/* LLM Calls Table */}
      {!isLoading && !error && data && viewTab === 'llm_calls' && (
        <Card>
          <CardContent className="p-0">
            {llmCalls.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground">
                <MagnifyingGlass className="h-8 w-8 mx-auto mb-2" />
                <p className="text-sm">No LLM calls found</p>
                <p className="text-xs mt-1">LLM calls will appear here as agents and chat interact with models</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8"></TableHead>
                    <TableHead className="whitespace-nowrap">Model</TableHead>
                    <TableHead className="whitespace-nowrap">Provider</TableHead>
                    <TableHead className="whitespace-nowrap">Status</TableHead>
                    <TableHead className="text-right whitespace-nowrap">Latency</TableHead>
                    <TableHead className="text-right whitespace-nowrap">Input</TableHead>
                    <TableHead className="text-right whitespace-nowrap">Output</TableHead>
                    <TableHead className="text-right whitespace-nowrap">Cost</TableHead>
                    <TableHead className="whitespace-nowrap">Time</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {llmCalls.map((call) => {
                    const isExpanded = expandedCalls.has(call.id);
                    const totalTokens = call.input_tokens + call.output_tokens;
                    const costPerKToken = totalTokens > 0
                      ? (call.cost_usd / totalTokens) * 1000
                      : 0;
                    const inputRatio = totalTokens > 0
                      ? ((call.input_tokens / totalTokens) * 100).toFixed(0)
                      : '0';

                    return (
                      <Fragment key={call.id}>
                        <TableRow className="cursor-pointer hover:bg-accent/50" onClick={() => toggleCall(call.id)}>
                          <TableCell className="w-8 px-2">
                            {isExpanded ? (
                              <CaretDown className="h-4 w-4 text-muted-foreground" />
                            ) : (
                              <CaretRight className="h-4 w-4 text-muted-foreground" />
                            )}
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline" className="text-xs font-mono">
                              {call.model}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {call.provider}
                          </TableCell>
                          <TableCell>
                            <StatusBadge
                              status={call.status === 'success' ? 'success' : 'error'}
                              label={call.status}
                              size="xs"
                            />
                          </TableCell>
                          <TableCell className="text-right font-mono text-xs">
                            {formatDuration(call.latency_ms)}
                          </TableCell>
                          <TableCell className="text-right text-xs">
                            {formatTokens(call.input_tokens)}
                          </TableCell>
                          <TableCell className="text-right text-xs">
                            {formatTokens(call.output_tokens)}
                          </TableCell>
                          <TableCell className="text-right text-xs font-mono">
                            {formatCost(call.cost_usd)}
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {format(new Date(call.created_at), 'MMM d, HH:mm:ss')}
                          </TableCell>
                        </TableRow>
                        {isExpanded && (
                          <tr>
                            <td colSpan={9} className="p-0" style={{ maxWidth: 0 }}>
                              <div className="bg-muted/30 border-t px-6 py-4 space-y-3 overflow-hidden">
                                {/* Token Breakdown */}
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                  <div>
                                    <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Total Tokens</p>
                                    <p className="text-sm font-mono font-medium">{totalTokens.toLocaleString()}</p>
                                  </div>
                                  <div>
                                    <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Cost per 1K tokens</p>
                                    <p className="text-sm font-mono font-medium">{formatCost(costPerKToken)}</p>
                                  </div>
                                  <div>
                                    <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Input / Output</p>
                                    <p className="text-sm font-mono font-medium">{inputRatio}% / {100 - Number(inputRatio)}%</p>
                                  </div>
                                  <div>
                                    <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Tokens/sec</p>
                                    <p className="text-sm font-mono font-medium">
                                      {call.latency_ms > 0
                                        ? Math.round((call.output_tokens / call.latency_ms) * 1000).toLocaleString()
                                        : '--'}
                                    </p>
                                  </div>
                                </div>

                                {/* Token distribution bar */}
                                <div>
                                  <div className="flex h-2 rounded-full overflow-hidden bg-muted">
                                    <div
                                      className="bg-blue-500/70 transition-all"
                                      style={{ width: `${inputRatio}%` }}
                                      title={`Input: ${call.input_tokens.toLocaleString()}`}
                                    />
                                    <div
                                      className="bg-emerald-500/70 transition-all"
                                      style={{ width: `${100 - Number(inputRatio)}%` }}
                                      title={`Output: ${call.output_tokens.toLocaleString()}`}
                                    />
                                  </div>
                                  <div className="flex justify-between mt-1">
                                    <span className="text-[10px] text-blue-600 dark:text-blue-400">Input {call.input_tokens.toLocaleString()}</span>
                                    <span className="text-[10px] text-emerald-600 dark:text-emerald-400">Output {call.output_tokens.toLocaleString()}</span>
                                  </div>
                                </div>

                                {/* Context metadata */}
                                <div className="flex items-center gap-4 text-[10px] text-muted-foreground pt-1 border-t">
                                  <span>ID: <span className="font-mono">{call.id.slice(0, 12)}</span></span>
                                  {call.agent_id && <span>Agent: <span className="font-mono">{call.agent_id.slice(0, 8)}</span></span>}
                                  {call.workflow_id && <span>Workflow: <span className="font-mono">{call.workflow_id.slice(0, 8)}</span></span>}
                                  <span>Full time: {format(new Date(call.created_at), 'yyyy-MM-dd HH:mm:ss')}</span>
                                </div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </TableBody>
              </Table>
              </div>
            )}
          </CardContent>

          {llmCalls.length > 0 && (
            <div className="flex items-center justify-end px-4 py-3 border-t gap-2">
              <Button variant="outline" size="sm" disabled={!canGoBack} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                <CaretLeft className="h-4 w-4" />
              </Button>
              <Button variant="outline" size="sm" disabled={!canGoForward} onClick={() => setOffset(offset + PAGE_SIZE)}>
                <CaretRight className="h-4 w-4" />
              </Button>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
