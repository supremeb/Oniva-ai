import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listSchedules,
  getSchedule,
  createSchedule,
  updateSchedule,
  deleteSchedule,
  pauseSchedule,
  resumeSchedule,
  triggerSchedule,
  listScheduleExecutions,
  ScheduleCreate,
  ScheduleUpdate,
} from '@/lib/api/schedules';

export function useSchedules(workflowId?: string, status?: string) {
  return useQuery({
    queryKey: ['schedules', { workflowId, status }],
    queryFn: () => listSchedules({ workflow_id: workflowId, status }),
    refetchInterval: 30_000,
    staleTime: 30000,
    retry: 1,
  });
}

export function useSchedule(scheduleId: string) {
  return useQuery({
    queryKey: ['schedules', scheduleId],
    queryFn: () => getSchedule(scheduleId),
    enabled: !!scheduleId,
    staleTime: 30000,
    retry: 1,
  });
}

export function useCreateSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: ScheduleCreate) => createSchedule(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] }).catch(() => {});
    },
  });
}

export function useUpdateSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ScheduleUpdate }) =>
      updateSchedule(id, data),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] }).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ['schedules', variables.id] }).catch(() => {});
    },
  });
}

export function useDeleteSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteSchedule(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] }).catch(() => {});
    },
  });
}

export function usePauseSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => pauseSchedule(id),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] }).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ['schedules', id] }).catch(() => {});
    },
  });
}

export function useResumeSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => resumeSchedule(id),
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] }).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ['schedules', id] }).catch(() => {});
    },
  });
}

export function useTriggerSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => triggerSchedule(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] }).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ['schedule-executions'] }).catch(() => {});
    },
  });
}

export function useScheduleExecutions(scheduleId: string) {
  return useQuery({
    queryKey: ['schedule-executions', scheduleId],
    queryFn: () => listScheduleExecutions(scheduleId),
    enabled: !!scheduleId,
    staleTime: 10000,
    retry: 1,
    // Adaptive refetch: 5s when running executions exist, 60s otherwise
    refetchInterval: (query) => {
      const hasRunning = query.state.data?.items?.some(
        (e) => e.status === 'running' || e.status === 'pending'
      );
      return hasRunning ? 5000 : 60000;
    },
  });
}
