'use client';

import { useState, useMemo } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import {
  Form,
  FormField,
  FormItem,
  FormLabel,
  FormControl,
  FormMessage,
  FormDescription,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Loader2, CheckCircle2, XCircle, Plus, X, Search } from 'lucide-react';
import {
  useCreateLLMProvider,
  useUpdateLLMProvider,
  useTestLLMProvider,
  useBrowseModels,
} from '@/hooks/useLLMProviders';
import { toast } from 'sonner';
import type { LLMProvider } from '@/lib/api/llm-providers';

// Which fields to show per provider type
interface ProviderFields {
  identifier: boolean;
  displayName: boolean;
  apiBase: boolean | 'required';
}

interface ProviderTypeConfig {
  label: string;
  description: string;
  apiBase: string;
  placeholder: string;
  docsUrl: string;
  defaultModels: readonly string[];
  fields: ProviderFields;
}

// Provider configurations with per-provider field visibility
const PROVIDER_TYPES: Record<string, ProviderTypeConfig> = {
  openai: {
    label: 'OpenAI',
    description: 'GPT-4o, GPT-4 Turbo, and other OpenAI models',
    apiBase: 'https://api.openai.com/v1',
    placeholder: 'sk-...',
    docsUrl: 'https://platform.openai.com/api-keys',
    defaultModels: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo'],
    fields: { identifier: false, displayName: false, apiBase: false },
  },
  anthropic: {
    label: 'Anthropic',
    description: 'Claude 3.5 Sonnet, Claude 3 Opus, and other Claude models',
    apiBase: 'https://api.anthropic.com/v1',
    placeholder: 'sk-ant-...',
    docsUrl: 'https://console.anthropic.com/settings/keys',
    defaultModels: ['claude-3-5-sonnet-20241022', 'claude-3-opus-20240229', 'claude-3-haiku-20240307'],
    fields: { identifier: false, displayName: false, apiBase: false },
  },
  openrouter: {
    label: 'OpenRouter',
    description: 'Access 100+ models through a single API',
    apiBase: 'https://openrouter.ai/api/v1',
    placeholder: 'sk-or-v1-...',
    docsUrl: 'https://openrouter.ai/keys',
    defaultModels: ['openai/gpt-4o-mini', 'anthropic/claude-3.5-sonnet', 'google/gemini-2.0-flash-exp:free', 'deepseek/deepseek-chat'],
    fields: { identifier: false, displayName: false, apiBase: false },
  },
  azure: {
    label: 'Azure OpenAI',
    description: 'Microsoft Azure-hosted OpenAI models',
    apiBase: '',
    placeholder: 'Your Azure API key',
    docsUrl: 'https://portal.azure.com',
    defaultModels: [],
    fields: { identifier: false, displayName: true, apiBase: 'required' },
  },
  google: {
    label: 'Google AI (Gemini)',
    description: 'Gemini 1.5 Pro, Gemini 1.5 Flash, and other Google models',
    apiBase: 'https://generativelanguage.googleapis.com/v1beta',
    placeholder: 'AIzaSy...',
    docsUrl: 'https://aistudio.google.com/app/apikey',
    defaultModels: ['gemini-1.5-pro', 'gemini-1.5-flash', 'gemini-pro'],
    fields: { identifier: false, displayName: false, apiBase: false },
  },
  custom: {
    label: 'Custom Endpoint',
    description: 'Self-hosted inference (vLLM, Ollama, etc.)',
    apiBase: '',
    placeholder: 'Your API key (if required)',
    docsUrl: '',
    defaultModels: [],
    fields: { identifier: true, displayName: true, apiBase: 'required' },
  },
};

type ProviderType = 'openai' | 'anthropic' | 'openrouter' | 'azure' | 'google' | 'custom';

const schema = z.object({
  name: z.string().min(1, 'Name is required').max(100),
  display_name: z.string().min(1, 'Display name is required').max(200),
  provider_type: z.enum(['openai', 'anthropic', 'openrouter', 'azure', 'google', 'custom']),
  api_base: z.string().nullable().optional(),
  api_key: z.string().nullable().optional(),
  models: z.array(z.string()),
  default_model: z.string().nullable().optional(),
  embedding_model: z.string().nullable().optional(),
  workflow_builder_model: z.string().nullable().optional(),
  is_enabled: z.boolean(),
});

type FormData = z.infer<typeof schema>;

interface LLMProviderFormProps {
  provider?: LLMProvider | null;
  onClose: () => void;
}

export function LLMProviderForm({ provider, onClose }: LLMProviderFormProps) {
  const isEditing = !!provider;
  const createProvider = useCreateLLMProvider();
  const updateProvider = useUpdateLLMProvider();
  const testProvider = useTestLLMProvider();

  const [testResult, setTestResult] = useState<{
    success: boolean;
    message?: string;
  } | null>(null);
  const [newModel, setNewModel] = useState('');
  const [browseOpen, setBrowseOpen] = useState(false);
  const [browseSearch, setBrowseSearch] = useState('');

  const form = useForm<FormData>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: provider?.name || '',
      display_name: provider?.display_name || '',
      provider_type: (provider?.provider_type as ProviderType) || 'openai',
      api_base: provider?.api_base || '',
      api_key: '', // Never pre-fill API key
      models: provider?.models || [],
      default_model: provider?.default_model || '',
      embedding_model: provider?.embedding_model || '',
      workflow_builder_model: provider?.workflow_builder_model || '',
      is_enabled: provider?.is_enabled ?? true,
    },
  });

  const selectedType = form.watch('provider_type') as ProviderType;
  const typeConfig = PROVIDER_TYPES[selectedType];
  const models = form.watch('models');

  // Browse models from provider catalog (only fetch when browse is open)
  const supportsBrowse = selectedType === 'openrouter';
  const { data: catalogModels = [], isLoading: catalogLoading } = useBrowseModels(
    selectedType,
    browseOpen && supportsBrowse,
  );
  const filteredCatalog = useMemo(() => {
    if (!browseSearch.trim()) return catalogModels;
    const q = browseSearch.toLowerCase();
    return catalogModels.filter(
      (m) => m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q),
    );
  }, [catalogModels, browseSearch]);

  // Field visibility helpers
  const showField = (field: keyof ProviderFields) => typeConfig.fields[field] !== false;
  const isRequired = (field: keyof ProviderFields) => typeConfig.fields[field] === 'required';

  const handleProviderTypeChange = (value: string) => {
    const config = PROVIDER_TYPES[value as ProviderType];
    form.setValue('provider_type', value as ProviderType);

    // Auto-fill hidden fields
    if (!config.fields.identifier) {
      form.setValue('name', value);
    }
    if (!config.fields.displayName) {
      form.setValue('display_name', config.label);
    }
    if (!config.fields.apiBase && config.apiBase) {
      form.setValue('api_base', config.apiBase);
    } else if (config.fields.apiBase) {
      form.setValue('api_base', '');
    }

    // Auto-populate default models for new providers
    if (!isEditing && config.defaultModels.length > 0) {
      form.setValue('models', [...config.defaultModels]);
      form.setValue('default_model', config.defaultModels[0] || '');
    } else {
      form.setValue('models', []);
      form.setValue('default_model', '');
    }

    setTestResult(null);
  };

  const handleAddModel = () => {
    if (newModel.trim() && !models.includes(newModel.trim())) {
      form.setValue('models', [...models, newModel.trim()]);
      setNewModel('');
    }
  };

  const handleRemoveModel = (model: string) => {
    form.setValue(
      'models',
      models.filter((m) => m !== model)
    );
    if (form.getValues('default_model') === model) {
      form.setValue('default_model', '');
    }
  };

  const handleToggleCatalogModel = (modelId: string) => {
    if (models.includes(modelId)) {
      handleRemoveModel(modelId);
    } else {
      form.setValue('models', [...models, modelId]);
    }
  };

  const handleAddDefaultModels = () => {
    const existingModels = new Set(models);
    const newModels = typeConfig.defaultModels.filter((m) => !existingModels.has(m));
    form.setValue('models', [...models, ...newModels]);
  };

  const handleTest = async () => {
    setTestResult(null);

    const data = form.getValues();
    const testApiKey = data.api_key || '';

    if (!testApiKey && !provider?.has_api_key) {
      toast.error('API key required', { description: 'Please enter an API key to test the connection.' });
      return;
    }

    try {
      const result = await testProvider.mutateAsync({
        provider_type: data.provider_type,
        api_base: data.api_base || undefined,
        api_key: testApiKey,
        model: data.default_model || data.models[0] || 'gpt-4o-mini',
      });

      setTestResult({
        success: result.success,
        message: result.success
          ? `Connected successfully using ${result.model}`
          : result.error || 'Connection failed',
      });
    } catch {
      setTestResult({
        success: false,
        message: 'Failed to test connection',
      });
    }
  };

  const onSubmit = async (data: FormData) => {
    // Auto-fill hidden fields before submit
    if (!typeConfig.fields.identifier) {
      data.name = data.provider_type;
    }
    if (!typeConfig.fields.displayName) {
      data.display_name = typeConfig.label;
    }
    if (!typeConfig.fields.apiBase && typeConfig.apiBase) {
      data.api_base = typeConfig.apiBase;
    }

    try {
      if (isEditing && provider) {
        await updateProvider.mutateAsync({
          id: provider.id,
          data: {
            display_name: data.display_name,
            api_base: data.api_base || null,
            api_key: data.api_key || undefined,
            models: data.models,
            default_model: data.default_model || null,
            embedding_model: data.embedding_model || null,
            workflow_builder_model: data.workflow_builder_model || null,
            is_enabled: data.is_enabled,
          },
        });
        toast('Provider updated', { description: `${data.display_name} has been updated.` });
      } else {
        await createProvider.mutateAsync({
          name: data.name,
          display_name: data.display_name,
          provider_type: data.provider_type,
          api_base: data.api_base || null,
          api_key: data.api_key || null,
          models: data.models,
          default_model: data.default_model || null,
          embedding_model: data.embedding_model || null,
          workflow_builder_model: data.workflow_builder_model || null,
          is_enabled: data.is_enabled,
        });
        toast('Provider added', { description: `${data.display_name} has been configured.` });
      }
      onClose();
    } catch {
      toast.error('Error', { description: isEditing ? 'Failed to update provider.' : 'Failed to create provider.' });
    }
  };

  const isPending = createProvider.isPending || updateProvider.isPending;

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-5">
            {/* Provider Type */}
            {!isEditing && (
              <FormField
                control={form.control}
                name="provider_type"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Provider</FormLabel>
                    <Select
                      onValueChange={handleProviderTypeChange}
                      value={field.value}
                    >
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder="Select provider" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {Object.entries(PROVIDER_TYPES).map(([key, config]) => (
                          <SelectItem key={key} value={key}>
                            {config.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            {/* Identifier — only for custom/azure providers on create */}
            {!isEditing && showField('identifier') && (
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Name</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="my-provider"
                        {...field}
                        onChange={(e) => {
                          const value = e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-');
                          field.onChange(value);
                        }}
                      />
                    </FormControl>
                    <FormDescription className="text-xs">
                      Unique identifier (lowercase, hyphens only)
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            {/* Display Name — only when provider needs custom naming */}
            {showField('displayName') && (
              <FormField
                control={form.control}
                name="display_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Display Name</FormLabel>
                    <FormControl>
                      <Input
                        placeholder={
                          selectedType === 'azure'
                            ? 'My Azure Deployment'
                            : 'My Custom Provider'
                        }
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            {/* API Base URL — only for azure/custom */}
            {showField('apiBase') && (
              <FormField
                control={form.control}
                name="api_base"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      API Base URL
                      {isRequired('apiBase') && (
                        <span className="text-destructive ml-1">*</span>
                      )}
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder={
                          selectedType === 'azure'
                            ? 'https://your-resource.openai.azure.com'
                            : 'https://your-endpoint.com/v1'
                        }
                        {...field}
                        value={field.value || ''}
                      />
                    </FormControl>
                    <FormDescription className="text-xs">
                      {selectedType === 'azure'
                        ? 'Your Azure OpenAI endpoint URL'
                        : 'Your inference endpoint (vLLM, Ollama, etc.)'}
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            {/* API Key — always shown */}
            <FormField
              control={form.control}
              name="api_key"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>
                    API Key
                    {isEditing && provider?.has_api_key && (
                      <Badge variant="secondary" className="ml-2 text-xs">
                        Configured
                      </Badge>
                    )}
                  </FormLabel>
                  <FormControl>
                    <Input
                      type="password"
                      placeholder={
                        isEditing && provider?.has_api_key
                          ? 'Leave empty to keep existing key'
                          : typeConfig.placeholder
                      }
                      {...field}
                      value={field.value || ''}
                      autoComplete="off"
                    />
                  </FormControl>
                  <FormDescription className="text-xs">
                    Encrypted and stored securely.{' '}
                    {typeConfig.docsUrl && (
                      <a
                        href={typeConfig.docsUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-primary hover:underline"
                      >
                        Get API key
                      </a>
                    )}
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Models */}
            <FormField
              control={form.control}
              name="models"
              render={() => (
                <FormItem>
                  <div className="flex items-center justify-between">
                    <FormLabel>Models ({models.length})</FormLabel>
                    {supportsBrowse && (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="h-7 text-xs gap-1.5"
                        onClick={() => setBrowseOpen(!browseOpen)}
                      >
                        <Search className="h-3 w-3" />
                        {browseOpen ? 'Close' : 'Browse Models'}
                      </Button>
                    )}
                  </div>
                  <div className="space-y-2">
                    {models.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {models.map((model) => (
                          <Badge
                            key={model}
                            variant="secondary"
                            className="text-xs gap-1 pr-1"
                          >
                            {model}
                            <button
                              type="button"
                              onClick={() => handleRemoveModel(model)}
                              className="ml-1 hover:text-destructive"
                            >
                              <X className="h-3 w-3" />
                            </button>
                          </Badge>
                        ))}
                      </div>
                    )}

                    {/* Browse Models picker */}
                    {browseOpen && supportsBrowse && (
                      <div className="rounded-lg border bg-background">
                        <div className="p-2 border-b">
                          <div className="relative">
                            <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
                            <Input
                              placeholder="Search models..."
                              value={browseSearch}
                              onChange={(e) => setBrowseSearch(e.target.value)}
                              className="h-8 pl-8 text-xs"
                            />
                          </div>
                        </div>
                        {catalogLoading ? (
                          <div className="flex items-center justify-center py-8">
                            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                            <span className="ml-2 text-xs text-muted-foreground">Loading models...</span>
                          </div>
                        ) : (
                          <ScrollArea className="h-[280px]">
                            <div className="p-1">
                              {filteredCatalog.map((m) => {
                                const isSelected = models.includes(m.id);
                                return (
                                  <button
                                    key={m.id}
                                    type="button"
                                    onClick={() => handleToggleCatalogModel(m.id)}
                                    className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-md text-left transition-colors ${
                                      isSelected ? 'bg-primary/5' : 'hover:bg-accent/50'
                                    }`}
                                  >
                                    <Checkbox
                                      checked={isSelected}
                                      className="pointer-events-none h-3.5 w-3.5"
                                    />
                                    <div className="flex-1 min-w-0">
                                      <div className="text-xs font-medium truncate">{m.name}</div>
                                      <div className="text-[10px] text-muted-foreground truncate">{m.id}</div>
                                    </div>
                                    {m.context_length && (
                                      <span className="text-[10px] text-muted-foreground shrink-0">
                                        {m.context_length >= 1000000
                                          ? `${(m.context_length / 1000000).toFixed(0)}M`
                                          : `${(m.context_length / 1000).toFixed(0)}K`} ctx
                                      </span>
                                    )}
                                  </button>
                                );
                              })}
                              {filteredCatalog.length === 0 && (
                                <div className="py-6 text-center text-xs text-muted-foreground">
                                  No models match &quot;{browseSearch}&quot;
                                </div>
                              )}
                            </div>
                          </ScrollArea>
                        )}
                      </div>
                    )}

                    {/* Manual add input */}
                    {!browseOpen && (
                      <>
                        <div className="flex gap-2">
                          <Input
                            placeholder="Add model name"
                            value={newModel}
                            onChange={(e) => setNewModel(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                e.preventDefault();
                                handleAddModel();
                              }
                            }}
                            className="flex-1"
                          />
                          <Button type="button" variant="outline" size="sm" onClick={handleAddModel}>
                            <Plus className="h-4 w-4" />
                          </Button>
                        </div>
                        {typeConfig.defaultModels.length > 0 && models.length === 0 && (
                          <Button
                            type="button"
                            variant="link"
                            size="sm"
                            className="text-xs h-auto p-0"
                            onClick={handleAddDefaultModels}
                          >
                            Add default {typeConfig.label} models
                          </Button>
                        )}
                      </>
                    )}
                  </div>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Default Model */}
            {models.length > 0 && (
              <FormField
                control={form.control}
                name="default_model"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Default Model</FormLabel>
                    <Select
                      onValueChange={field.onChange}
                      value={field.value || ''}
                    >
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder="Select default model" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {models.map((model) => (
                          <SelectItem key={model} value={model}>
                            {model}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            {/* Embedding Model */}
            <FormField
              control={form.control}
              name="embedding_model"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Embedding Model</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="e.g. qwen/qwen3-embedding-8b"
                      {...field}
                      value={field.value || ''}
                    />
                  </FormControl>
                  <FormDescription className="text-xs">
                    Model used for knowledge base embeddings. Leave empty to use the system default.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Workflow Builder Model */}
            <FormField
              control={form.control}
              name="workflow_builder_model"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Workflow Builder Model</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="e.g. openai/gpt-4o"
                      {...field}
                      value={field.value || ''}
                    />
                  </FormControl>
                  <FormDescription className="text-xs">
                    Model used by the AI workflow builder chat. Leave empty to use gpt-4o.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Enabled Toggle */}
            <FormField
              control={form.control}
              name="is_enabled"
              render={({ field }) => (
                <FormItem className="flex items-center justify-between rounded-lg border p-3">
                  <div className="space-y-0.5">
                    <FormLabel className="text-sm">Enable Provider</FormLabel>
                    <FormDescription className="text-xs">
                      Include this provider in the failover chain
                    </FormDescription>
                  </div>
                  <FormControl>
                    <Switch checked={field.value} onCheckedChange={field.onChange} />
                  </FormControl>
                </FormItem>
              )}
            />

            {/* Test Connection */}
            <div className="flex items-center gap-2 p-3 bg-muted/50 rounded-lg">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleTest}
                disabled={testProvider.isPending}
              >
                {testProvider.isPending ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : null}
                Test Connection
              </Button>
              {testResult && (
                <div className="flex items-center gap-2 text-sm">
                  {testResult.success ? (
                    <CheckCircle2 className="h-4 w-4 text-green-500 shrink-0" />
                  ) : (
                    <XCircle className="h-4 w-4 text-red-500 shrink-0" />
                  )}
                  <span className={testResult.success ? 'text-green-600' : 'text-red-600'}>
                    {testResult.message}
                  </span>
                </div>
              )}
            </div>

            {/* Actions */}
            <div className="flex justify-end gap-2 pt-2 border-t border-border">
              <Button type="button" variant="outline" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" disabled={isPending}>
                {isPending ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : null}
                {isEditing ? 'Save Changes' : 'Add Provider'}
              </Button>
            </div>
      </form>
    </Form>
  );
}
