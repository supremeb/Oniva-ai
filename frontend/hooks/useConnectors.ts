import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  getConnectors,
  createConnector,
  updateConnector,
  deleteConnector,
  testConnector,
} from '@/lib/api/settings';
import { CreateConnectorRequest } from '@/types/api';

export function useConnectors() {
  return useQuery({
    queryKey: ['connectors'],
    queryFn: getConnectors,
  });
}

export function useCreateConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createConnector,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['connectors'] });
    },
  });
}

export function useUpdateConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<CreateConnectorRequest> }) =>
      updateConnector(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['connectors'] });
    },
  });
}

export function useDeleteConnector() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteConnector,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['connectors'] });
    },
  });
}

export function useTestConnector() {
  return useMutation({
    mutationFn: testConnector,
  });
}
