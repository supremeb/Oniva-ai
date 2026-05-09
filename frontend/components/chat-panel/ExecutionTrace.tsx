'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import {
  CaretDown,
  CaretRight,
  CheckCircle,
  XCircle,
  CircleNotch,
  SkipForward,
  Clock,
  Lightning,
  Brain,
  DownloadSimple,
} from '@phosphor-icons/react';
import type { ExecutionTraceStep } from '@/lib/types/chat-panels';
import { cn } from '@/lib/utils';

interface ExecutionTraceProps {
  steps: ExecutionTraceStep[];
  currentStepId?: string;
  compact?: boolean;
  contextName?: string; // For download filename
}

const STATUS_ICONS = {
  pending: Clock,
  running: CircleNotch,
  completed: CheckCircle,
  failed: XCircle,
  skipped: SkipForward,
};

const STATUS_COLORS = {
  pending: 'text-muted-foreground',
  running: 'text-blue-500',
  completed: 'text-green-500',
  failed: 'text-destructive',
  skipped: 'text-muted-foreground',
};

export function ExecutionTrace({
  steps,
  currentStepId,
  compact = false,
  contextName = 'execution',
}: ExecutionTraceProps) {
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());

  const downloadLogs = () => {
    // Create structured log data
    const logData = {
      exportedAt: new Date().toISOString(),
      context: contextName,
      totalSteps: steps.length,
      summary: {
        completed: steps.filter((s) => s.status === 'completed').length,
        failed: steps.filter((s) => s.status === 'failed').length,
        running: steps.filter((s) => s.status === 'running').length,
        pending: steps.filter((s) => s.status === 'pending').length,
        skipped: steps.filter((s) => s.status === 'skipped').length,
      },
      totalDurationMs: steps.reduce((acc, s) => acc + (s.duration_ms || 0), 0),
      steps: steps.map((step, index) => ({
        stepNumber: index + 1,
        stepId: step.step_id,
        nodeId: step.node_id,
        nodeType: step.node_type,
        status: step.status,
        reasoning: step.reasoning,
        input: step.input,
        output: step.output,
        error: step.error,
        startedAt: step.started_at,
        completedAt: step.completed_at,
        durationMs: step.duration_ms,
      })),
    };

    // Create blob and download
    const blob = new Blob([JSON.stringify(logData, null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${contextName.replace(/[^a-z0-9]/gi, '-')}-trace-${new Date().toISOString().split('T')[0]}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const toggleStep = (stepId: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepId)) {
        next.delete(stepId);
      } else {
        next.add(stepId);
      }
      return next;
    });
  };

  const expandAll = () => {
    setExpandedSteps(new Set(steps.map((s) => s.step_id)));
  };

  const collapseAll = () => {
    setExpandedSteps(new Set());
  };

  const formatDuration = (ms?: number) => {
    if (!ms) return '';
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  };

  if (steps.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-8 text-muted-foreground">
        <Lightning className="h-8 w-8 mb-2 opacity-50" />
        <p className="text-sm">No execution steps yet</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b">
        <div className="flex items-center gap-2">
          <Lightning className="h-4 w-4" />
          <span className="font-medium text-sm">Execution Trace</span>
          <Badge variant="secondary" className="text-xs">
            {steps.length} steps
          </Badge>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={downloadLogs}
            title="Download Logs"
          >
            <DownloadSimple className="h-4 w-4" />
          </Button>
          {!compact && (
            <>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                onClick={expandAll}
              >
                Expand All
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                onClick={collapseAll}
              >
                Collapse
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Steps */}
      <ScrollArea className="flex-1">
        <div className={cn('space-y-1', compact ? 'p-2' : 'p-3')}>
          {steps.map((step, index) => {
            const StatusIcon = STATUS_ICONS[step.status];
            const isExpanded = expandedSteps.has(step.step_id);
            const isCurrent = step.step_id === currentStepId;

            return (
              <Collapsible
                key={step.step_id}
                open={isExpanded}
                onOpenChange={() => toggleStep(step.step_id)}
              >
                <div
                  className={cn(
                    'rounded-lg border transition-colors',
                    isCurrent && 'ring-2 ring-primary ring-offset-1',
                    step.status === 'failed' && 'border-destructive/50',
                    step.status === 'completed' && 'border-green-500/30'
                  )}
                >
                  <CollapsibleTrigger asChild>
                    <button
                      className={cn(
                        'w-full flex items-center gap-2 p-2 text-left hover:bg-accent/50 transition-colors rounded-lg',
                        compact && 'p-1.5'
                      )}
                    >
                      <div className="flex items-center gap-2 flex-1 min-w-0">
                        <span className="text-xs text-muted-foreground w-5">
                          {index + 1}.
                        </span>
                        <StatusIcon
                          className={cn(
                            'h-4 w-4 flex-shrink-0',
                            STATUS_COLORS[step.status],
                            step.status === 'running' && 'animate-spin'
                          )}
                          weight={step.status === 'completed' ? 'fill' : 'regular'}
                        />
                        <span className="text-sm font-medium truncate">
                          {step.node_type || 'Step'}
                        </span>
                        {step.node_id && (
                          <Badge variant="outline" className="text-xs font-mono">
                            {step.node_id}
                          </Badge>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        {step.duration_ms && (
                          <span className="text-xs text-muted-foreground">
                            {formatDuration(step.duration_ms)}
                          </span>
                        )}
                        {isExpanded ? (
                          <CaretDown className="h-4 w-4 text-muted-foreground" />
                        ) : (
                          <CaretRight className="h-4 w-4 text-muted-foreground" />
                        )}
                      </div>
                    </button>
                  </CollapsibleTrigger>

                  <CollapsibleContent>
                    <div className="px-2 pb-2 space-y-2 border-t">
                      {/* Reasoning */}
                      {step.reasoning && (
                        <div className="mt-2">
                          <div className="flex items-center gap-1 text-xs font-medium text-muted-foreground mb-1">
                            <Brain className="h-3 w-3" />
                            Reasoning
                          </div>
                          <p className="text-xs bg-muted/50 p-2 rounded">
                            {step.reasoning}
                          </p>
                        </div>
                      )}

                      {/* Input */}
                      {step.input && Object.keys(step.input).length > 0 && (
                        <div>
                          <div className="text-xs font-medium text-muted-foreground mb-1">
                            Input
                          </div>
                          <pre className="text-xs bg-muted/50 p-2 rounded overflow-x-auto">
                            {JSON.stringify(step.input, null, 2)}
                          </pre>
                        </div>
                      )}

                      {/* Output */}
                      {step.output && Object.keys(step.output).length > 0 && (
                        <div>
                          <div className="text-xs font-medium text-muted-foreground mb-1">
                            Output
                          </div>
                          <pre className="text-xs bg-muted/50 p-2 rounded overflow-x-auto max-h-32 overflow-y-auto">
                            {JSON.stringify(step.output, null, 2)}
                          </pre>
                        </div>
                      )}

                      {/* Error */}
                      {step.error && (
                        <div>
                          <div className="text-xs font-medium text-destructive mb-1">
                            Error
                          </div>
                          <p className="text-xs bg-destructive/10 text-destructive p-2 rounded">
                            {step.error}
                          </p>
                        </div>
                      )}

                      {/* Timestamps */}
                      {(step.started_at || step.completed_at) && (
                        <div className="flex items-center gap-4 text-xs text-muted-foreground pt-1">
                          {step.started_at && (
                            <span>
                              Started: {new Date(step.started_at).toLocaleTimeString()}
                            </span>
                          )}
                          {step.completed_at && (
                            <span>
                              Completed: {new Date(step.completed_at).toLocaleTimeString()}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  </CollapsibleContent>
                </div>
              </Collapsible>
            );
          })}
        </div>
      </ScrollArea>
    </div>
  );
}
