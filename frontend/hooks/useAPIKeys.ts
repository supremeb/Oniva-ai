import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getAPIKeys, createAPIKey, deleteAPIKey, updateAPIKey } from '@/lib/api/settings';
import { CreateAPIKeyRequest } from '@/types/api';

export function useAPIKeys() {
  return useQuery({
    queryKey: ['api-keys'],
    queryFn: getAPIKeys,
  });
}

export function useCreateAPIKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createAPIKey,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
    },
  });
}

export function useUpdateAPIKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<CreateAPIKeyRequest> }) =>
      updateAPIKey(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
    },
  });
}

export function useDeleteAPIKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteAPIKey,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
    },
  });
}
