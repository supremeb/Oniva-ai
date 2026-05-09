'use client';

import { useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import {
  CircleNotch,
  CheckCircle,
  XCircle,
  X,
  Lightning,
} from '@phosphor-icons/react';
import { useBackgroundJobStore, selectActiveJobs } from '@/lib/stores/background-job';
import { cancelJob } from '@/lib/api/background-jobs';
import type { BackgroundJob } from '@/lib/types/chat-panels';
import { cn } from '@/lib/utils';
import { formatDistanceToNow, parseISO } from 'date-fns';

interface BackgroundJobIndicatorProps {
  contextKey?: string; // Filter to specific context
  showPopover?: boolean;
}

export function BackgroundJobIndicator({
  contextKey,
  showPopover = true,
}: BackgroundJobIndicatorProps) {
  const activeJobs = useBackgroundJobStore(selectActiveJobs);
  const allJobs = useBackgroundJobStore((state) => state.activeJobs);
  const { startJobStream, removeJob } = useBackgroundJobStore();

  // Filter jobs by context if provided
  const filteredJobs = contextKey
    ? activeJobs.filter((job) => {
        const jobContext = job.workflow_id
          ? `workflow:${job.workflow_id}`
          : job.agent_id
          ? `agent:${job.agent_id}`
          : '';
        return jobContext === contextKey;
      })
    : activeJobs;

  // Auto-start streaming for active jobs
  useEffect(() => {
    filteredJobs.forEach((job) => {
      if (job.status === 'running' || job.status === 'pending') {
        startJobStream(job.id);
      }
    });
  }, [filteredJobs, startJobStream]);

  const handleCancel = async (jobId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await cancelJob(jobId, 'Cancelled by user');
    } catch (error) {
      console.error('Failed to cancel job:', error);
    }
  };

  const handleDismiss = (jobId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    removeJob(jobId);
  };

  if (filteredJobs.length === 0) {
    return null;
  }

  // Get the primary job (most recent running or first pending)
  const primaryJob =
    filteredJobs.find((j) => j.status === 'running') || filteredJobs[0];

  const indicator = (
    <div
      className={cn(
        'flex items-center gap-2 px-3 py-1.5 rounded-full',
        'bg-primary/10 border border-primary/20',
        'transition-all duration-300'
      )}
    >
      <CircleNotch className="h-4 w-4 animate-spin text-primary" />
      <span className="text-sm font-medium">
        {primaryJob.status === 'running'
          ? `Running (${primaryJob.progress}%)`
          : 'Pending'}
      </span>
      {filteredJobs.length > 1 && (
        <Badge variant="secondary" className="text-xs">
          +{filteredJobs.length - 1}
        </Badge>
      )}
    </div>
  );

  if (!showPopover) {
    return indicator;
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button className="cursor-pointer">{indicator}</button>
      </PopoverTrigger>
      <PopoverContent className="w-80" align="end">
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h4 className="font-medium">Background Jobs</h4>
            <Badge variant="outline">{filteredJobs.length} active</Badge>
          </div>

          <div className="space-y-2 max-h-60 overflow-y-auto">
            {filteredJobs.map((job) => (
              <JobItem
                key={job.id}
                job={job}
                onCancel={handleCancel}
                onDismiss={handleDismiss}
              />
            ))}
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

interface JobItemProps {
  job: BackgroundJob;
  onCancel: (jobId: string, e: React.MouseEvent) => void;
  onDismiss: (jobId: string, e: React.MouseEvent) => void;
}

function JobItem({ job, onCancel, onDismiss }: JobItemProps) {
  const isCompleted = job.status === 'completed' || job.status === 'failed';

  return (
    <div
      className={cn(
        'p-3 rounded-lg border',
        job.status === 'failed' && 'border-destructive/50 bg-destructive/5',
        job.status === 'completed' && 'border-green-500/50 bg-green-50'
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          {job.status === 'running' && (
            <CircleNotch className="h-4 w-4 animate-spin text-primary flex-shrink-0" />
          )}
          {job.status === 'completed' && (
            <CheckCircle
              className="h-4 w-4 text-green-500 flex-shrink-0"
              weight="fill"
            />
          )}
          {job.status === 'failed' && (
            <XCircle
              className="h-4 w-4 text-destructive flex-shrink-0"
              weight="fill"
            />
          )}
          {job.status === 'pending' && (
            <Lightning className="h-4 w-4 text-muted-foreground flex-shrink-0" />
          )}
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium truncate">
              {job.workflow_name || job.agent_name || 'Job'}
            </p>
            <p className="text-xs text-muted-foreground">
              {job.job_type.replace('_', ' ')}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {!isCompleted && (
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6"
              onClick={(e) => onCancel(job.id, e)}
              title="Cancel"
            >
              <X className="h-3 w-3" />
            </Button>
          )}
          {isCompleted && (
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6"
              onClick={(e) => onDismiss(job.id, e)}
              title="Dismiss"
            >
              <X className="h-3 w-3" />
            </Button>
          )}
        </div>
      </div>

      {/* Progress bar for running jobs */}
      {job.status === 'running' && (
        <div className="mt-2">
          <Progress value={job.progress} className="h-1" />
          <p className="text-xs text-muted-foreground mt-1">
            {job.progress}% complete
          </p>
        </div>
      )}

      {/* Error message for failed jobs */}
      {job.status === 'failed' && job.error && (
        <p className="text-xs text-destructive mt-2 truncate">{job.error}</p>
      )}

      {/* Duration for completed jobs */}
      {isCompleted && job.duration_ms && (
        <p className="text-xs text-muted-foreground mt-2">
          Completed in {(job.duration_ms / 1000).toFixed(1)}s
        </p>
      )}

      {/* Created time */}
      <p className="text-xs text-muted-foreground mt-1">
        Started {formatDistanceToNow(parseISO(job.created_at), { addSuffix: true })}
      </p>
    </div>
  );
}
