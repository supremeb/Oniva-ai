'use client';

import { useState, useMemo, useCallback } from 'react';
import { useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { formatDistanceToNow, format } from 'date-fns';
import {
  Plus,
  Pause,
  Play,
  PencilSimple,
  Trash,
  Lightning,
  CalendarBlank,
  Clock,
  ArrowClockwise,
  CircleNotch,
  CaretDown,
  CaretUp,
  TreeStructure,
  FunnelSimple,
} from '@phosphor-icons/react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { ConfirmationDialog } from '@/components/ui/confirmation-dialog';
import { ScheduleDialog } from '@/components/schedules/ScheduleDialog';
import { ExecutionHistory } from '@/components/schedules/ExecutionHistory';
import {
  useSchedules,
  useDeleteSchedule,
  usePauseSchedule,
  useResumeSchedule,
  useTriggerSchedule,
} from '@/hooks/useSchedules';
import { useWorkflows } from '@/hooks/useWorkflow';
import { toast } from 'sonner';
import type { ScheduleResponse } from '@/lib/api/schedules';

const SCHEDULE_TYPE_LABELS: Record<string, string> = {
  cron: 'Cron',
  interval: 'Interval',
  one_time: 'One-Time',
};

const STATUS_STYLES: Record<string, string> = {
  active: 'bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-400',
  paused: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-950 dark:text-yellow-400',
  completed: 'bg-muted text-muted-foreground',
  disabled: 'bg-muted text-muted-foreground',
  error: 'bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400',
};

const LAST_RUN_STATUS_STYLES: Record<string, string> = {
  completed: 'text-green-600 dark:text-green-400',
  failed: 'text-red-600 dark:text-red-400',
  running: 'text-blue-600 dark:text-blue-400',
  pending: 'text-muted-foreground',
  skipped: 'text-yellow-600 dark:text-yellow-400',
};

export default function SchedulesPage() {
  const searchParams = useSearchParams();
  const workflowIdFilter = searchParams.get('workflow_id') || undefined;

  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [showDialog, setShowDialog] = useState(false);
  const [editSchedule, setEditSchedule] = useState<ScheduleResponse | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ScheduleResponse | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data: schedulesData, isLoading } = useSchedules(
    workflowIdFilter,
    statusFilter === 'all' ? undefined : statusFilter
  );
  const { data: workflowsData } = useWorkflows();
  const deleteMutation = useDeleteSchedule();
  const pauseMutation = usePauseSchedule();
  const resumeMutation = useResumeSchedule();
  const triggerMutation = useTriggerSchedule();

  const schedules = schedulesData?.items || [];
  const workflows = useMemo(
    () =>
      (workflowsData || []).map((w: { id: string; name: string }) => ({
        id: w.id,
        name: w.name,
      })),
    [workflowsData]
  );

  const workflowNameMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const w of workflows) {
      map[w.id] = w.name;
    }
    return map;
  }, [workflows]);

  const handleDelete = useCallback(async () => {
    if (!deleteTarget) return;
    try {
      await deleteMutation.mutateAsync(deleteTarget.id);
      toast('Schedule deleted', { description: `"${deleteTarget.name}" has been removed.` });
    } catch {
      toast.error('Failed to delete schedule');
    }
    setDeleteTarget(null);
  }, [deleteTarget, deleteMutation]);

  const handlePause = useCallback(
    async (schedule: ScheduleResponse) => {
      try {
        await pauseMutation.mutateAsync(schedule.id);
        toast('Schedule paused', { description: `"${schedule.name}" has been paused.` });
      } catch {
        toast.error('Failed to pause schedule');
      }
    },
    [pauseMutation]
  );

  const handleResume = useCallback(
    async (schedule: ScheduleResponse) => {
      try {
        await resumeMutation.mutateAsync(schedule.id);
        toast('Schedule resumed', { description: `"${schedule.name}" has been resumed.` });
      } catch {
        toast.error('Failed to resume schedule');
      }
    },
    [resumeMutation]
  );

  const handleTrigger = useCallback(
    async (schedule: ScheduleResponse) => {
      try {
        await triggerMutation.mutateAsync(schedule.id);
        toast('Execution triggered', {
          description: `"${schedule.name}" will run shortly.`,
        });
      } catch {
        toast.error('Failed to trigger execution');
      }
    },
    [triggerMutation]
  );

  const handleEdit = useCallback((schedule: ScheduleResponse) => {
    setEditSchedule(schedule);
    setShowDialog(true);
  }, []);

  return (
    <div className="flex-1 p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Schedules</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Automate workflow executions with cron, interval, or one-time schedules.
          </p>
        </div>
        <Button
          onClick={() => {
            setEditSchedule(null);
            setShowDialog(true);
          }}
          className="gap-1.5"
        >
          <Plus className="h-4 w-4" />
          New Schedule
        </Button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <FunnelSimple className="h-4 w-4" />
          Status
        </div>
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="w-36 h-8 text-sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All</SelectItem>
            <SelectItem value="active">Active</SelectItem>
            <SelectItem value="paused">Paused</SelectItem>
            <SelectItem value="completed">Completed</SelectItem>
            <SelectItem value="error">Error</SelectItem>
          </SelectContent>
        </Select>

        {workflowIdFilter && (
          <Badge variant="secondary" className="gap-1">
            <TreeStructure className="h-3 w-3" />
            {workflowNameMap[workflowIdFilter] || 'Filtered workflow'}
          </Badge>
        )}

        {schedulesData && (
          <span className="text-xs text-muted-foreground ml-auto">
            {schedulesData.total} schedule{schedulesData.total !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <CircleNotch className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : schedules.length === 0 ? (
        <div className="text-center py-16 border border-dashed rounded-xl">
          <CalendarBlank className="h-10 w-10 mx-auto text-muted-foreground/50 mb-3" />
          <p className="text-muted-foreground">No schedules yet.</p>
          <p className="text-sm text-muted-foreground/70 mt-1">
            Create one to automate your workflows.
          </p>
          <Button
            variant="outline"
            className="mt-4 gap-1.5"
            onClick={() => {
              setEditSchedule(null);
              setShowDialog(true);
            }}
          >
            <Plus className="h-4 w-4" />
            Create Schedule
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          {schedules.map((schedule) => {
            const isExpanded = expandedId === schedule.id;

            return (
              <div key={schedule.id} className="rounded-xl border bg-card">
                {/* Schedule Row */}
                <div className="flex items-center gap-4 px-4 py-3">
                  {/* Expand toggle */}
                  <button
                    onClick={() => setExpandedId(isExpanded ? null : schedule.id)}
                    className="p-1 rounded hover:bg-muted text-muted-foreground"
                  >
                    {isExpanded ? (
                      <CaretUp className="h-4 w-4" />
                    ) : (
                      <CaretDown className="h-4 w-4" />
                    )}
                  </button>

                  {/* Name + Workflow */}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm truncate">{schedule.name}</span>
                      <Badge
                        variant="outline"
                        className="text-[10px] px-1.5 py-0 shrink-0"
                      >
                        {SCHEDULE_TYPE_LABELS[schedule.schedule_type] || schedule.schedule_type}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-1.5 mt-0.5">
                      <Link
                        href={`/workflows/${schedule.workflow_id}`}
                        className="text-xs text-muted-foreground hover:text-foreground hover:underline truncate"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <TreeStructure className="h-3 w-3 inline mr-1" />
                        {workflowNameMap[schedule.workflow_id] || 'Unknown workflow'}
                      </Link>
                    </div>
                  </div>

                  {/* Human-readable schedule */}
                  <div className="hidden md:block text-xs text-muted-foreground min-w-[140px]">
                    <div className="flex items-center gap-1">
                      <Clock className="h-3 w-3 shrink-0" />
                      <span className="truncate">
                        {schedule.human_readable_schedule || schedule.cron_expression || '-'}
                      </span>
                    </div>
                  </div>

                  {/* Next run */}
                  <div className="hidden lg:block text-xs min-w-[100px]">
                    {schedule.next_run_at ? (
                      <div>
                        <span className="text-muted-foreground">Next: </span>
                        {format(new Date(schedule.next_run_at), 'MMM d, HH:mm')}
                      </div>
                    ) : (
                      <span className="text-muted-foreground">-</span>
                    )}
                  </div>

                  {/* Last run status */}
                  <div className="hidden lg:block text-xs min-w-[80px]">
                    {schedule.last_run_status ? (
                      <span
                        className={
                          LAST_RUN_STATUS_STYLES[schedule.last_run_status] ||
                          'text-muted-foreground'
                        }
                      >
                        {schedule.last_run_status}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">Never run</span>
                    )}
                  </div>

                  {/* Status badge */}
                  <span
                    className={`px-2 py-0.5 text-xs font-medium rounded-md ${
                      STATUS_STYLES[schedule.status] || STATUS_STYLES.disabled
                    }`}
                  >
                    {schedule.status}
                  </span>

                  {/* Actions */}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0">
                        <svg
                          xmlns="http://www.w3.org/2000/svg"
                          width="16"
                          height="16"
                          viewBox="0 0 256 256"
                        >
                          <circle cx="128" cy="60" r="16" fill="currentColor" />
                          <circle cx="128" cy="128" r="16" fill="currentColor" />
                          <circle cx="128" cy="196" r="16" fill="currentColor" />
                        </svg>
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onClick={() => handleEdit(schedule)}>
                        <PencilSimple className="h-4 w-4 mr-2" />
                        Edit
                      </DropdownMenuItem>
                      {schedule.status === 'active' ? (
                        <DropdownMenuItem onClick={() => handlePause(schedule)}>
                          <Pause className="h-4 w-4 mr-2" />
                          Pause
                        </DropdownMenuItem>
                      ) : schedule.status === 'paused' ? (
                        <DropdownMenuItem onClick={() => handleResume(schedule)}>
                          <Play className="h-4 w-4 mr-2" />
                          Resume
                        </DropdownMenuItem>
                      ) : null}
                      <DropdownMenuItem onClick={() => handleTrigger(schedule)}>
                        <Lightning className="h-4 w-4 mr-2" />
                        Run Now
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        className="text-destructive"
                        onClick={() => setDeleteTarget(schedule)}
                      >
                        <Trash className="h-4 w-4 mr-2" />
                        Delete
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>

                {/* Expanded: Execution History */}
                {isExpanded && (
                  <div className="px-4 pb-4 pt-1 border-t">
                    <ExecutionHistory scheduleId={schedule.id} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Schedule Create/Edit Dialog */}
      <ScheduleDialog
        open={showDialog}
        onOpenChange={(open) => {
          setShowDialog(open);
          if (!open) setEditSchedule(null);
        }}
        workflowId={workflowIdFilter}
        workflowName={workflowIdFilter ? workflowNameMap[workflowIdFilter] : undefined}
        workflows={workflows}
        editSchedule={editSchedule}
      />

      {/* Delete Confirmation */}
      <ConfirmationDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title="Delete Schedule"
        description={`This will permanently delete "${deleteTarget?.name}" and all its execution history. This cannot be undone.`}
        onConfirm={handleDelete}
      />
    </div>
  );
}
