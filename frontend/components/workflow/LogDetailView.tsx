'use client';

import React, { useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import {
  CaretLeft,
  CaretDown,
  CaretRight,
  XCircle,
  Copy,
  Download,
  Lightning,
  Code,
  Calendar,
} from '@phosphor-icons/react';
import { useWorkflowRunTree } from '@/hooks/useWorkflowRunTree';
import { RunStatusBadge } from '@/components/run/RunStatusBadge';
import { WorkflowRunTree } from './WorkflowRunTree';
import { normalizeStatus } from '@/lib/run/status';
import type { ExecutionTraceStep, WorkflowLogDetail } from '@/lib/api/workflow-logs';

interface LogDetailViewProps {
  workflowId: string;
  runId: string;
  onBack: () => void;
}

const triggerLabels = {
  manual: { icon: Lightning, label: 'Manual Run' },
  api: { icon: Code, label: 'API Trigger' },
  scheduled: { icon: Calendar, label: 'Scheduled' },
};

function formatDuration(ms: number | null): string {
  if (ms === null) return '-';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
}

function formatTime(isoString: string | null): string {
  if (!isoString) return '-';
  return new Date(isoString).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function LogDetailView({ workflowId, runId, onBack }: LogDetailViewProps) {
  const { log, tree, waiting, decide, isLoading, error } = useWorkflowRunTree(workflowId, runId);

  if (isLoading) {
    return <LogDetailSkeleton onBack={onBack} />;
  }

  if (error || !log) {
    return (
      <Card className="h-full flex flex-col">
        <Header onBack={onBack} />
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-muted-foreground">
            <XCircle className="h-12 w-12 mx-auto mb-4 text-red-400" />
            <p className="text-sm">Failed to load log details</p>
            <Button variant="outline" size="sm" className="mt-4" onClick={onBack}>
              Go Back
            </Button>
          </div>
        </div>
      </Card>
    );
  }

  const status = normalizeStatus(log.status, 'workflow_log');
  const trigger = triggerLabels[log.trigger] || triggerLabels.manual;
  const TriggerIcon = trigger.icon;

  return (
    <Card className="h-full flex flex-col">
      {/* Header with Back Button */}
      <Header onBack={onBack}>
        <div className="flex items-center gap-3">
          <div>
            <div className="flex items-center gap-2">
              <RunStatusBadge status={status} testId="log-detail-status" className="text-xs" />
              <Badge variant="outline" className="text-xs">
                <TriggerIcon className="h-3 w-3 mr-1" />
                {trigger.label}
              </Badge>
            </div>
            <p className="text-sm text-muted-foreground">
              {formatTime(log.started_at)}
            </p>
          </div>
        </div>
      </Header>

      <ScrollArea className="flex-1">
        <div className="p-4 space-y-4">
          {/* Summary Stats */}
          <div className="grid grid-cols-4 gap-3">
            <StatCard label="Duration" value={formatDuration(log.duration_ms)} />
            <StatCard label="Tokens" value={log.total_tokens.toLocaleString()} />
            <StatCard label="Nodes" value={log.nodes_executed.toString()} />
            <StatCard label="Progress" value={`${log.progress}%`} />
          </div>

          {/* Error Banner */}
          {log.error && (
            <div className="p-4 bg-red-50 border border-red-200 rounded-lg">
              <div className="flex items-center gap-2 text-red-700 font-medium mb-2">
                <XCircle className="h-4 w-4" />
                Error
              </div>
              <pre className="text-sm text-red-600 whitespace-pre-wrap font-mono overflow-x-auto">
                {log.error}
              </pre>
            </div>
          )}

          {/* Input Data */}
          {log.input_data && Object.keys(log.input_data).length > 0 && (
            <CollapsibleSection title="Input Data" defaultOpen={false}>
              <DataBlock data={log.input_data} />
            </CollapsibleSection>
          )}

          {/* Result */}
          {log.result && Object.keys(log.result).length > 0 && (
            <CollapsibleSection title="Result" defaultOpen={false}>
              <DataBlock data={log.result} />
            </CollapsibleSection>
          )}

          {/* Run tree: engine tasks while live, the stored trace once finished */}
          <div className="border rounded-lg">
            <div className="flex items-center justify-between p-3 border-b bg-muted/30">
              <span className="font-medium text-sm">Run</span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => downloadTrace(log)}
                className="h-7 text-xs"
              >
                <Download className="h-3 w-3 mr-1" />
                Export JSON
              </Button>
            </div>
            <div className="p-2">
              {tree.length === 0 || (tree[0].children.length === 0 && log.execution_trace.length === 0) ? (
                <div className="p-8 text-center text-muted-foreground text-sm">
                  No execution steps recorded
                </div>
              ) : (
                <WorkflowRunTree tree={tree} waiting={waiting} onDecide={decide} aria-label="Workflow run" />
              )}
            </div>
          </div>

          {/* Per-step input, output and token detail from the stored trace */}
          {log.execution_trace.length > 0 && (
            <CollapsibleSection title="Step details" defaultOpen={false}>
              <div className="divide-y">
                {log.execution_trace.map((step, index) => (
                  <ExecutionStepRow key={step.step_id || index} step={step} index={index} />
                ))}
              </div>
            </CollapsibleSection>
          )}
        </div>
      </ScrollArea>
    </Card>
  );
}

// ============================================
// Sub-components
// ============================================

function Header({ onBack, children }: { onBack: () => void; children?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 p-4 border-b">
      <Button variant="ghost" size="sm" onClick={onBack} className="h-8">
        <CaretLeft className="h-4 w-4 mr-1" />
        Back
      </Button>
      {children}
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="p-3 bg-muted/50 rounded-lg">
      <p className="text-xs text-muted-foreground mb-1">{label}</p>
      <p className="font-semibold text-sm">{value}</p>
    </div>
  );
}

function CollapsibleSection({
  title,
  children,
  defaultOpen = true,
}: {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <div className="border rounded-lg">
        <CollapsibleTrigger asChild>
          <button className="w-full flex items-center justify-between p-3 hover:bg-muted/50 transition-colors">
            <span className="font-medium text-sm">{title}</span>
            {isOpen ? (
              <CaretDown className="h-4 w-4 text-muted-foreground" />
            ) : (
              <CaretRight className="h-4 w-4 text-muted-foreground" />
            )}
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="p-3 pt-0">{children}</div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}

function ExecutionStepRow({ step, index }: { step: ExecutionTraceStep; index: number }) {
  const [isOpen, setIsOpen] = useState(step.status === 'failed');
  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <CollapsibleTrigger asChild>
        <button className="w-full p-3 text-left hover:bg-muted/50 transition-colors">
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted-foreground w-6 text-right">{index + 1}</span>
            <RunStatusBadge status={normalizeStatus(step.status, 'execution_trace')} />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium text-sm truncate">
                  {step.node_name || step.node_id}
                </span>
                <Badge variant="secondary" className="text-xs">
                  {step.node_type}
                </Badge>
              </div>
            </div>
            {step.duration_ms != null && (
              <span className="text-xs text-muted-foreground">
                {formatDuration(step.duration_ms)}
              </span>
            )}
            {isOpen ? (
              <CaretDown className="h-4 w-4 text-muted-foreground" />
            ) : (
              <CaretRight className="h-4 w-4 text-muted-foreground" />
            )}
          </div>
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="px-12 pb-3 space-y-3">
          {step.input && (
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">Input</p>
              <DataBlock data={step.input} maxHeight="max-h-32" />
            </div>
          )}
          {step.output && (
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">Output</p>
              <DataBlock data={step.output} maxHeight="max-h-32" />
            </div>
          )}
          {step.error && (
            <div className="p-2 bg-red-50 rounded text-sm text-red-600 font-mono">
              {step.error}
            </div>
          )}
          {step.reasoning && (
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">Reasoning</p>
              <p className="text-sm text-muted-foreground">{step.reasoning}</p>
            </div>
          )}
          {step.token_usage && (
            <div className="flex gap-4 text-xs text-muted-foreground">
              <span>Prompt: {step.token_usage.prompt?.toLocaleString() ?? 0}</span>
              <span>Completion: {step.token_usage.completion?.toLocaleString() ?? 0}</span>
              <span>Total: {step.token_usage.total?.toLocaleString() ?? 0}</span>
            </div>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function DataBlock({
  data,
  maxHeight = 'max-h-48',
}: {
  data: Record<string, unknown>;
  maxHeight?: string;
}) {
  const json = JSON.stringify(data, null, 2);
  const truncated = json.length > 1000;

  const handleCopy = () => {
    navigator.clipboard.writeText(json);
  };

  return (
    <div className="border rounded">
      <div className="flex items-center justify-end px-2 py-1 bg-muted/50 border-b">
        <Button variant="ghost" size="sm" className="h-6" onClick={handleCopy}>
          <Copy className="h-3 w-3" />
        </Button>
      </div>
      <pre className={`p-2 text-xs font-mono overflow-x-auto ${maxHeight} overflow-y-auto`}>
        {truncated ? json.slice(0, 1000) + '\n...' : json}
      </pre>
    </div>
  );
}

function LogDetailSkeleton({ onBack }: { onBack: () => void }) {
  return (
    <Card className="h-full flex flex-col">
      <Header onBack={onBack}>
        <Skeleton className="h-10 w-48" />
      </Header>
      <div className="p-4 space-y-4">
        <div className="grid grid-cols-4 gap-3">
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
        <Skeleton className="h-64" />
      </div>
    </Card>
  );
}

function downloadTrace(log: WorkflowLogDetail) {
  const blob = new Blob([JSON.stringify(log, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `workflow-log-${log.id}.json`;
  a.click();
  URL.revokeObjectURL(url);
}
