/**
 * React Query hooks for user management
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listUsers,
  getUser,
  updateUser,
  changeUserRole,
  deactivateUser,
  reactivateUser,
  adminResetPassword,
  listPendingUsers,
  addPendingUser,
  deletePendingUser,
  type User,
  type PendingUser,
} from '@/lib/api/users';

// =========================================
// Users Hooks
// =========================================

export function useUsers(params?: {
  search?: string;
  role?: string;
  is_active?: boolean;
  limit?: number;
  offset?: number;
}) {
  return useQuery({
    queryKey: ['users', params],
    queryFn: () => listUsers(params),
  });
}

export function useUser(userId: string) {
  return useQuery({
    queryKey: ['users', userId],
    queryFn: () => getUser(userId),
    enabled: !!userId,
  });
}

export function useUpdateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, data }: { userId: string; data: { name?: string } }) =>
      updateUser(userId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
  });
}

export function useChangeUserRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) =>
      changeUserRole(userId, role),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
  });
}

export function useDeactivateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deactivateUser,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
  });
}

export function useReactivateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: reactivateUser,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
  });
}

export function useAdminResetPassword() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, newPassword }: { userId: string; newPassword: string }) =>
      adminResetPassword(userId, newPassword),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
  });
}

// =========================================
// Pending Users Hooks
// =========================================

export function usePendingUsers(params?: {
  limit?: number;
  offset?: number;
}) {
  return useQuery({
    queryKey: ['pending-users', params],
    queryFn: () => listPendingUsers(params),
  });
}

export function useAddPendingUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ email, role }: { email: string; role: string }) =>
      addPendingUser(email, role),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pending-users'] });
    },
  });
}

export function useDeletePendingUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deletePendingUser,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pending-users'] });
    },
  });
}
