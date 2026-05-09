'use client';

import { useState, useEffect, useMemo } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  Form,
  FormField,
  FormItem,
  FormLabel,
  FormControl,
  FormMessage,
  FormDescription,
} from '@/components/ui/form';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { CircleNotch, CaretDown, CaretUp } from '@phosphor-icons/react';
import { useCreateSchedule, useUpdateSchedule } from '@/hooks/useSchedules';
import { toast } from 'sonner';
import type { ScheduleResponse } from '@/lib/api/schedules';

// Common timezones for quick selection
const COMMON_TIMEZONES = [
  'UTC',
  'US/Eastern',
  'US/Central',
  'US/Mountain',
  'US/Pacific',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Asia/Tokyo',
  'Asia/Shanghai',
  'Asia/Kolkata',
  'Australia/Sydney',
];

const scheduleSchema = z.object({
  name: z.string().min(1, 'Name is required').max(255),
  description: z.string().optional(),
  workflow_id: z.string().min(1, 'Workflow is required'),
  schedule_type: z.enum(['cron', 'interval', 'one_time']),
  cron_expression: z.string().optional(),
  interval_seconds: z.number().min(60).optional(),
  scheduled_time: z.string().optional(),
  timezone: z.string().min(1, 'Timezone is required'),
  input_variables: z.string().optional(),
  max_executions: z.number().min(1).nullable().optional(),
  max_concurrent: z.number().min(1).max(10).optional(),
  timeout_seconds: z.number().min(30).max(86400).optional(),
  retry_on_failure: z.boolean().optional(),
  max_retries: z.number().min(0).max(10).optional(),
  retry_delay_seconds: z.number().min(1).max(3600).optional(),
});

type ScheduleFormData = z.infer<typeof scheduleSchema>;

interface ScheduleDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workflowId?: string;
  workflowName?: string;
  workflows?: { id: string; name: string }[];
  editSchedule?: ScheduleResponse | null;
}

export function ScheduleDialog({
  open,
  onOpenChange,
  workflowId,
  workflowName,
  workflows = [],
  editSchedule,
}: ScheduleDialogProps) {
  const isEditing = !!editSchedule;
  const createSchedule = useCreateSchedule();
  const updateSchedule = useUpdateSchedule();
  const [showAdvanced, setShowAdvanced] = useState(false);

  const browserTimezone = useMemo(() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch {
      return 'UTC';
    }
  }, []);

  const form = useForm<ScheduleFormData>({
    resolver: zodResolver(scheduleSchema),
    defaultValues: {
      name: '',
      description: '',
      workflow_id: workflowId || '',
      schedule_type: 'cron',
      cron_expression: '',
      interval_seconds: 3600,
      scheduled_time: '',
      timezone: browserTimezone,
      input_variables: '{}',
      max_executions: null,
      max_concurrent: 1,
      timeout_seconds: 300,
      retry_on_failure: false,
      max_retries: 3,
      retry_delay_seconds: 60,
    },
  });

  const scheduleType = form.watch('schedule_type');

  // Reset form when dialog opens/closes or editSchedule changes
  useEffect(() => {
    if (!open) return;

    if (editSchedule) {
      form.reset({
        name: editSchedule.name,
        description: editSchedule.description || '',
        workflow_id: editSchedule.workflow_id,
        schedule_type: editSchedule.schedule_type,
        cron_expression: editSchedule.cron_expression || '',
        interval_seconds: editSchedule.interval_seconds || 3600,
        scheduled_time: editSchedule.scheduled_time
          ? editSchedule.scheduled_time.slice(0, 16)
          : '',
        timezone: editSchedule.timezone,
        input_variables: JSON.stringify(editSchedule.input_variables || {}, null, 2),
        max_executions: editSchedule.max_executions,
        max_concurrent: editSchedule.max_concurrent,
        timeout_seconds: editSchedule.timeout_seconds,
        retry_on_failure: editSchedule.retry_on_failure,
        max_retries: editSchedule.max_retries,
        retry_delay_seconds: editSchedule.retry_delay_seconds,
      });
    } else {
      form.reset({
        name: '',
        description: '',
        workflow_id: workflowId || '',
        schedule_type: 'cron',
        cron_expression: '',
        interval_seconds: 3600,
        scheduled_time: '',
        timezone: browserTimezone,
        input_variables: '{}',
        max_executions: null,
        max_concurrent: 1,
        timeout_seconds: 300,
        retry_on_failure: false,
        max_retries: 3,
        retry_delay_seconds: 60,
      });
    }
    setShowAdvanced(false);
  }, [open, editSchedule, workflowId, browserTimezone, form]);

  const onSubmit = async (data: ScheduleFormData) => {
    // Parse input variables
    let inputVars: Record<string, unknown> = {};
    if (data.input_variables && data.input_variables.trim() !== '{}') {
      try {
        inputVars = JSON.parse(data.input_variables);
      } catch {
        form.setError('input_variables', { message: 'Invalid JSON' });
        return;
      }
    }

    const payload = {
      name: data.name,
      description: data.description || undefined,
      workflow_id: data.workflow_id,
      schedule_type: data.schedule_type,
      cron_expression: data.schedule_type === 'cron' ? data.cron_expression : undefined,
      interval_seconds: data.schedule_type === 'interval' ? data.interval_seconds : undefined,
      scheduled_time: data.schedule_type === 'one_time' ? data.scheduled_time : undefined,
      timezone: data.timezone,
      input_variables: inputVars,
      max_executions: data.max_executions || undefined,
      max_concurrent: data.max_concurrent,
      timeout_seconds: data.timeout_seconds,
      retry_on_failure: data.retry_on_failure,
      max_retries: data.max_retries,
      retry_delay_seconds: data.retry_delay_seconds,
    };

    try {
      if (isEditing && editSchedule) {
        const { workflow_id: _wid, ...updateData } = payload;
        await updateSchedule.mutateAsync({ id: editSchedule.id, data: updateData });
        toast('Schedule updated', { description: `"${data.name}" has been updated.` });
      } else {
        await createSchedule.mutateAsync(payload);
        toast('Schedule created', { description: `"${data.name}" has been created.` });
      }
      onOpenChange(false);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'An error occurred';
      toast.error(isEditing ? 'Failed to update schedule' : 'Failed to create schedule', {
        description: message,
      });
    }
  };

  const isPending = createSchedule.isPending || updateSchedule.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[540px] max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEditing ? 'Edit Schedule' : 'New Schedule'}</DialogTitle>
          <DialogDescription>
            {isEditing
              ? 'Update the schedule configuration.'
              : 'Create a scheduled execution for a workflow.'}
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            {/* Workflow selector */}
            <FormField
              control={form.control}
              name="workflow_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Workflow</FormLabel>
                  {workflowId ? (
                    <div className="text-sm text-muted-foreground px-3 py-2 rounded-md border bg-muted/50">
                      {workflowName || workflowId}
                    </div>
                  ) : (
                    <Select onValueChange={field.onChange} value={field.value}>
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder="Select a workflow" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {workflows.map((w) => (
                          <SelectItem key={w.id} value={w.id}>
                            {w.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Name */}
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input placeholder="e.g., Daily market analysis" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Description */}
            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Description</FormLabel>
                  <FormControl>
                    <Input placeholder="Optional description" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Schedule Type Tabs */}
            <FormField
              control={form.control}
              name="schedule_type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Schedule Type</FormLabel>
                  <Tabs value={field.value} onValueChange={field.onChange}>
                    <TabsList className="w-full">
                      <TabsTrigger value="cron" className="flex-1">
                        Cron
                      </TabsTrigger>
                      <TabsTrigger value="interval" className="flex-1">
                        Interval
                      </TabsTrigger>
                      <TabsTrigger value="one_time" className="flex-1">
                        One-Time
                      </TabsTrigger>
                    </TabsList>

                    <TabsContent value="cron" className="mt-3">
                      <FormField
                        control={form.control}
                        name="cron_expression"
                        render={({ field: cronField }) => (
                          <FormItem>
                            <FormControl>
                              <Input
                                placeholder="e.g., 0 9 * * 1-5 (weekdays at 9am)"
                                {...cronField}
                              />
                            </FormControl>
                            <FormDescription>
                              Standard 5-field cron: minute hour day month weekday
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </TabsContent>

                    <TabsContent value="interval" className="mt-3">
                      <FormField
                        control={form.control}
                        name="interval_seconds"
                        render={({ field: intervalField }) => (
                          <FormItem>
                            <div className="flex items-center gap-2">
                              <FormControl>
                                <Input
                                  type="number"
                                  min={60}
                                  value={
                                    intervalField.value
                                      ? Math.floor(intervalField.value / 60)
                                      : ''
                                  }
                                  onChange={(e) => {
                                    const mins = parseInt(e.target.value) || 0;
                                    intervalField.onChange(mins * 60);
                                  }}
                                  className="w-24"
                                />
                              </FormControl>
                              <span className="text-sm text-muted-foreground">minutes</span>
                            </div>
                            <FormDescription>
                              Minimum interval is 1 minute
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </TabsContent>

                    <TabsContent value="one_time" className="mt-3">
                      <FormField
                        control={form.control}
                        name="scheduled_time"
                        render={({ field: timeField }) => (
                          <FormItem>
                            <FormControl>
                              <Input type="datetime-local" {...timeField} />
                            </FormControl>
                            <FormDescription>
                              The workflow will execute once at this time
                            </FormDescription>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </TabsContent>
                  </Tabs>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Timezone */}
            <FormField
              control={form.control}
              name="timezone"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Timezone</FormLabel>
                  <Select onValueChange={field.onChange} value={field.value}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Select timezone" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {COMMON_TIMEZONES.map((tz) => (
                        <SelectItem key={tz} value={tz}>
                          {tz}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Input Variables */}
            <FormField
              control={form.control}
              name="input_variables"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Input Variables (JSON)</FormLabel>
                  <FormControl>
                    <Textarea
                      placeholder='{"key": "value"}'
                      className="font-mono text-xs h-20"
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    Variables passed to the workflow on each execution
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Advanced Settings (collapsible) */}
            <button
              type="button"
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              {showAdvanced ? (
                <CaretUp className="h-3.5 w-3.5" />
              ) : (
                <CaretDown className="h-3.5 w-3.5" />
              )}
              Advanced Settings
            </button>

            {showAdvanced && (
              <div className="space-y-4 rounded-lg border p-4">
                <div className="grid grid-cols-2 gap-4">
                  <FormField
                    control={form.control}
                    name="max_executions"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Max Executions</FormLabel>
                        <FormControl>
                          <Input
                            type="number"
                            min={1}
                            placeholder="Unlimited"
                            value={field.value ?? ''}
                            onChange={(e) => {
                              const val = e.target.value;
                              field.onChange(val ? parseInt(val) : null);
                            }}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="max_concurrent"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Max Concurrent</FormLabel>
                        <FormControl>
                          <Input
                            type="number"
                            min={1}
                            max={10}
                            {...field}
                            onChange={(e) => field.onChange(parseInt(e.target.value) || 1)}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="timeout_seconds"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Timeout (seconds)</FormLabel>
                        <FormControl>
                          <Input
                            type="number"
                            min={30}
                            max={86400}
                            {...field}
                            onChange={(e) => field.onChange(parseInt(e.target.value) || 300)}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="retry_delay_seconds"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Retry Delay (seconds)</FormLabel>
                        <FormControl>
                          <Input
                            type="number"
                            min={1}
                            max={3600}
                            {...field}
                            onChange={(e) => field.onChange(parseInt(e.target.value) || 60)}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <FormField
                    control={form.control}
                    name="retry_on_failure"
                    render={({ field }) => (
                      <FormItem className="flex items-center gap-3">
                        <FormLabel className="text-sm mt-0">Retry on failure</FormLabel>
                        <FormControl>
                          <Switch checked={field.value} onCheckedChange={field.onChange} />
                        </FormControl>
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="max_retries"
                    render={({ field }) => (
                      <FormItem className="flex items-center gap-2">
                        <FormLabel className="text-sm mt-0 whitespace-nowrap">
                          Max retries
                        </FormLabel>
                        <FormControl>
                          <Input
                            type="number"
                            min={0}
                            max={10}
                            className="w-16 h-8"
                            {...field}
                            onChange={(e) => field.onChange(parseInt(e.target.value) || 0)}
                          />
                        </FormControl>
                      </FormItem>
                    )}
                  />
                </div>
              </div>
            )}

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isPending}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isPending}>
                {isPending && <CircleNotch className="h-4 w-4 animate-spin mr-1.5" />}
                {isEditing ? 'Update Schedule' : 'Create Schedule'}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
