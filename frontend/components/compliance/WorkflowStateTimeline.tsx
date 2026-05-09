'use client';

import { type FC } from 'react';
import { format } from 'date-fns';
import { parseTimestamp } from '@/lib/utils';
import {
  Play,
  Pause,
  CheckCircle,
  Warning,
  ArrowRight,
  Database,
  Brain,
  Shield,
} from '@phosphor-icons/react';
import type { WorkflowReplay } from '@/lib/api/compliance';

interface WorkflowStateTimelineProps {
  replay: WorkflowReplay;
  className?: string;
}

export const WorkflowStateTimeline: FC<WorkflowStateTimelineProps> = ({
  replay,
  className = '',
}) => {
  const getStateIcon = (stateName: string) => {
    if (stateName.includes('init')) return <Play weight="duotone" />;
    if (stateName.includes('process')) return <Brain weight="duotone" />;
    if (stateName.includes('data')) return <Database weight="duotone" />;
    if (stateName.includes('check') || stateName.includes('valid'))
      return <Shield weight="duotone" />;
    if (stateName.includes('complete')) return <CheckCircle weight="duotone" />;
    return <ArrowRight weight="duotone" />;
  };

  const formatDuration = (ms: number) => {
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
  };

  return (
    <div className={`space-y-6 ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-foreground">
            Workflow Execution Timeline
          </h3>
          <p className="text-xs text-muted-foreground mt-1">
            {replay.states.length} states • {formatDuration(replay.total_duration_ms)} total
          </p>
        </div>
        <div className="flex items-center gap-2">
          {replay.can_replay && (
            <span className="px-2 py-0.5 text-xs bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 rounded-md">
              Replayable
            </span>
          )}
          <span className="px-2 py-0.5 text-xs bg-muted text-muted-foreground border border-border rounded-md">
            {replay.checkpoints} checkpoints
          </span>
        </div>
      </div>

      {/* Timeline */}
      <div className="relative">
        {/* Vertical line */}
        <div className="absolute left-4 top-8 bottom-8 w-px bg-gradient-to-b from-primary/30 via-border to-transparent" />

        {/* States */}
        <div className="space-y-4">
          {replay.states.map((state, index) => (
            <div
              key={index}
              className="relative flex gap-4"
            >
              {/* Node */}
              <div
                className={`
                  relative z-10 w-8 h-8
                  rounded-full border-2
                  flex items-center justify-center
                  transition-all duration-300
                  ${
                    state.decision_point
                      ? 'bg-amber-500/10 border-amber-500/30 text-amber-500'
                      : 'bg-primary/10 border-primary/30 text-primary'
                  }
                `}
              >
                <div className="w-4 h-4">{getStateIcon(state.state_name)}</div>

                {state.decision_point && (
                  <div className="absolute inset-0 rounded-full animate-ping bg-amber-500/20" />
                )}
              </div>

              {/* Content */}
              <div className="flex-1 pb-4">
                <div className="bg-card border border-border/60 rounded-lg p-4 space-y-3">
                  {/* State header */}
                  <div className="flex items-start justify-between">
                    <div>
                      <h4 className="text-sm font-semibold text-foreground capitalize">
                        {state.state_name.replace(/_/g, ' ')}
                      </h4>
                      <p className="text-xs text-muted-foreground font-mono mt-0.5">
                        {format(parseTimestamp(state.timestamp), 'HH:mm:ss.SSS')}
                      </p>
                    </div>
                    {index < replay.states.length - 1 && (
                      <span className="text-xs text-muted-foreground/70 font-mono">
                        +
                        {formatDuration(
                          parseTimestamp(replay.states[index + 1].timestamp).getTime() -
                            parseTimestamp(state.timestamp).getTime()
                        )}
                      </span>
                    )}
                  </div>

                  {/* Tools used */}
                  {state.tools_used.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {state.tools_used.map((tool, toolIndex) => (
                        <span
                          key={toolIndex}
                          className="
                            px-2 py-0.5 text-xs
                            bg-muted border border-border
                            rounded-md text-muted-foreground
                          "
                        >
                          {tool}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Decision point */}
                  {state.decision_point && (
                    <div className="bg-amber-500/5 border border-amber-500/20 rounded-lg p-3 space-y-2">
                      <div className="flex items-center gap-2">
                        <Warning className="h-4 w-4 text-amber-500" />
                        <span className="text-xs font-medium text-amber-600 dark:text-amber-400">
                          Decision Point
                        </span>
                        <span className="ml-auto text-xs text-amber-500/70">
                          {Math.round(state.decision_point.confidence * 100)}% confidence
                        </span>
                      </div>
                      <div className="space-y-1">
                        <p className="text-xs text-muted-foreground">Options considered:</p>
                        <div className="flex flex-wrap gap-1">
                          {state.decision_point.options.map((option, optIndex) => (
                            <span
                              key={optIndex}
                              className={`
                                px-2 py-0.5 text-xs rounded-md border
                                ${
                                  option === state.decision_point?.selected
                                    ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20'
                                    : 'bg-muted text-muted-foreground border-border'
                                }
                              `}
                            >
                              {option}
                              {option === state.decision_point?.selected && ' ✓'}
                            </span>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Variables preview */}
                  {Object.keys(state.variables).length > 0 && (
                    <details className="group">
                      <summary className="text-xs text-muted-foreground cursor-pointer hover:text-foreground transition-colors">
                        View state variables ({Object.keys(state.variables).length})
                      </summary>
                      <div className="mt-2 p-2 bg-muted/50 rounded-md">
                        <pre className="text-xs text-muted-foreground font-mono overflow-x-auto">
                          {JSON.stringify(state.variables, null, 2)}
                        </pre>
                      </div>
                    </details>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* End marker */}
        <div className="relative flex gap-4">
          <div className="relative z-10 w-8 h-8 rounded-full bg-card border-2 border-border flex items-center justify-center">
            <Pause weight="fill" className="w-4 h-4 text-muted-foreground" />
          </div>
          <div className="flex-1">
            <p className="text-xs text-muted-foreground mt-2">Workflow completed</p>
          </div>
        </div>
      </div>
    </div>
  );
};
