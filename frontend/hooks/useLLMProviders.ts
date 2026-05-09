import { useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  getLLMProviders,
  getLLMProvider,
  createLLMProvider,
  updateLLMProvider,
  deleteLLMProvider,
  testLLMProvider,
  reorderLLMProviders,
  getLLMUsage,
  browseAvailableModels,
  type LLMProvider,
  type CreateLLMProviderRequest,
  type UpdateLLMProviderRequest,
  type TestProviderRequest,
  type BrowseModelInfo,
} from '@/lib/api/llm-providers';
import { SUPPORTED_MODELS, type ModelDefinition } from '@/lib/models';

export function useLLMProviders() {
  return useQuery({
    queryKey: ['llm-providers'],
    queryFn: getLLMProviders,
  });
}

export function useLLMProvider(id: string) {
  return useQuery({
    queryKey: ['llm-providers', id],
    queryFn: () => getLLMProvider(id),
    enabled: !!id,
  });
}

export function useCreateLLMProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateLLMProviderRequest) => createLLMProvider(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm-providers'] });
    },
  });
}

export function useUpdateLLMProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: UpdateLLMProviderRequest }) =>
      updateLLMProvider(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm-providers'] });
    },
  });
}

export function useDeleteLLMProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteLLMProvider,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm-providers'] });
    },
  });
}

export function useTestLLMProvider() {
  return useMutation({
    mutationFn: (data: TestProviderRequest) => testLLMProvider(data),
  });
}

export function useReorderLLMProviders() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: reorderLLMProviders,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm-providers'] });
    },
  });
}

export function useLLMUsage(days: number = 30) {
  return useQuery({
    queryKey: ['llm-usage', days],
    queryFn: () => getLLMUsage(days),
  });
}

export function useBrowseModels(providerType: string, enabled = false) {
  return useQuery({
    queryKey: ['browse-models', providerType],
    queryFn: () => browseAvailableModels(providerType),
    staleTime: 300000, // 5 minutes
    enabled,
  });
}

/**
 * Map provider_type to display name for model grouping.
 */
const PROVIDER_DISPLAY_NAMES: Record<string, ModelDefinition['provider']> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  google: 'Google',
  openrouter: 'OpenRouter',
};

/**
 * Derive available models from the tenant's configured LLM providers.
 *
 * When providers are loaded, builds a model list from each enabled provider's
 * `models` array. Falls back to the static SUPPORTED_MODELS when providers
 * haven't loaded yet or the tenant has no providers configured.
 */
export function useAvailableModels() {
  const { data: providers, isLoading, isError } = useLLMProviders();

  const models = useMemo<ModelDefinition[]>(() => {
    // Fall back to static list while loading or on error
    if (isLoading || isError || !providers || providers.length === 0) {
      return SUPPORTED_MODELS;
    }

    const result: ModelDefinition[] = [];
    for (const provider of providers) {
      if (!provider.is_enabled) continue;

      const displayProvider =
        PROVIDER_DISPLAY_NAMES[provider.provider_type] ?? 'OpenRouter';

      for (const modelId of provider.models) {
        // Check if SUPPORTED_MODELS already has metadata for this model
        const existing = SUPPORTED_MODELS.find((m) => m.id === modelId);
        if (existing) {
          result.push({ ...existing, provider: displayProvider });
        } else {
          // Build a display name from the model ID
          const shortName = modelId.includes('/')
            ? modelId.split('/').pop()!
            : modelId;
          const displayName = shortName
            .replace(/-/g, ' ')
            .replace(/\b\w/g, (c) => c.toUpperCase());

          result.push({
            id: modelId,
            name: displayName,
            provider: displayProvider,
            capabilities: ['chat', 'streaming'],
          });
        }
      }
    }

    return result.length > 0 ? result : SUPPORTED_MODELS;
  }, [providers, isLoading, isError]);

  return { models, isLoading };
}
