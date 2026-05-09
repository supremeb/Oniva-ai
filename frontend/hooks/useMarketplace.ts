/**
 * TanStack Query hooks for Connector Marketplace
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listCategories,
  listConnectors,
  getConnectorDetail,
  enableConnector,
  importCustomSpec,
  getConnectorOperations,
  testConnection,
  listEnabledConnectors,
  disableConnector,
  type EnableConnectorRequest,
  type ImportOpenAPIRequest,
} from '@/lib/api/marketplace';

// Query keys
export const marketplaceKeys = {
  all: ['marketplace'] as const,
  categories: () => [...marketplaceKeys.all, 'categories'] as const,
  connectors: (params?: { category?: string; search?: string }) =>
    [...marketplaceKeys.all, 'connectors', params] as const,
  connector: (id: string) => [...marketplaceKeys.all, 'connector', id] as const,
  operations: (id: string) =>
    [...marketplaceKeys.all, 'operations', id] as const,
  enabled: () => [...marketplaceKeys.all, 'enabled'] as const,
};

/**
 * Hook to fetch connector categories.
 */
export function useCategories() {
  return useQuery({
    queryKey: marketplaceKeys.categories(),
    queryFn: listCategories,
    staleTime: 5 * 60 * 1000, // 5 minutes
  });
}

/**
 * Hook to list connectors from registry.
 */
export function useConnectors(params?: { category?: string; search?: string }) {
  return useQuery({
    queryKey: marketplaceKeys.connectors(params),
    queryFn: () => listConnectors(params),
    staleTime: 60 * 1000, // 1 minute
  });
}

/**
 * Hook to get connector detail.
 */
export function useConnectorDetail(connectorId: string | undefined) {
  return useQuery({
    queryKey: marketplaceKeys.connector(connectorId ?? ''),
    queryFn: () => getConnectorDetail(connectorId!),
    enabled: !!connectorId,
    staleTime: 60 * 1000,
  });
}

/**
 * Hook to get connector operations.
 */
export function useConnectorOperations(connectorId: string | undefined) {
  return useQuery({
    queryKey: marketplaceKeys.operations(connectorId ?? ''),
    queryFn: () => getConnectorOperations(connectorId!),
    enabled: !!connectorId,
    staleTime: 5 * 60 * 1000,
  });
}

/**
 * Hook to list enabled connectors.
 */
export function useEnabledConnectors() {
  return useQuery({
    queryKey: marketplaceKeys.enabled(),
    queryFn: listEnabledConnectors,
    staleTime: 30 * 1000, // 30 seconds
  });
}

/**
 * Hook to enable a connector.
 */
export function useEnableConnector() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      connectorId,
      request,
    }: {
      connectorId: string;
      request: EnableConnectorRequest;
    }) => enableConnector(connectorId, request),
    onSuccess: () => {
      // Invalidate relevant queries
      queryClient.invalidateQueries({ queryKey: marketplaceKeys.connectors() });
      queryClient.invalidateQueries({ queryKey: marketplaceKeys.enabled() });
    },
  });
}

/**
 * Hook to import a custom OpenAPI spec.
 */
export function useImportSpec() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (request: ImportOpenAPIRequest) => importCustomSpec(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: marketplaceKeys.enabled() });
    },
  });
}

/**
 * Hook to test connector connection.
 */
export function useTestConnection() {
  return useMutation({
    mutationFn: ({
      connectorUuid,
      credentials,
    }: {
      connectorUuid: string;
      credentials?: Record<string, string>;
    }) => testConnection(connectorUuid, credentials),
  });
}

/**
 * Hook to disable a connector.
 */
export function useDisableConnector() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (connectorUuid: string) => disableConnector(connectorUuid),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: marketplaceKeys.connectors() });
      queryClient.invalidateQueries({ queryKey: marketplaceKeys.enabled() });
    },
  });
}
