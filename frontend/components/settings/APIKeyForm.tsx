'use client';

import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Form, FormField, FormItem, FormLabel, FormControl, FormMessage, FormDescription } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { AlertCircle } from 'lucide-react';
import { useCreateAPIKey } from '@/hooks/useAPIKeys';
import { APIError } from '@/lib/api';

// Provider configuration with metadata
const PROVIDERS = {
  openrouter: {
    label: 'OpenRouter',
    description: 'Access 100+ LLM models through a single API (Recommended)',
    placeholder: 'sk-or-v1-...',
    docsUrl: 'https://openrouter.ai/keys',
    category: 'llm',
  },
  openai: {
    label: 'OpenAI',
    description: 'GPT-4o, GPT-4 Turbo, and other OpenAI models',
    placeholder: 'sk-...',
    docsUrl: 'https://platform.openai.com/api-keys',
    category: 'llm',
  },
  anthropic: {
    label: 'Anthropic',
    description: 'Claude 3.5 Sonnet, Claude 3 Opus, and other Claude models',
    placeholder: 'sk-ant-...',
    docsUrl: 'https://console.anthropic.com/settings/keys',
    category: 'llm',
  },
  google: {
    label: 'Google AI (Gemini)',
    description: 'Gemini 1.5 Pro, Gemini 1.5 Flash, and other Google models',
    placeholder: 'AIzaSy...',
    docsUrl: 'https://aistudio.google.com/app/apikey',
    category: 'llm',
  },
  parallelai: {
    label: 'Parallel AI',
    description: 'High-performance AI inference with optimized latency',
    placeholder: 'pai-...',
    docsUrl: 'https://www.parallelai.com/docs/api-keys',
    category: 'llm',
  },
  exa: {
    label: 'Exa.ai',
    description: 'Neural web search for AI applications',
    placeholder: 'exa-...',
    docsUrl: 'https://dashboard.exa.ai/api-keys',
    category: 'tools',
  },
  perplexity: {
    label: 'Perplexity',
    description: 'Deep work with real-time web access',
    placeholder: 'pplx-...',
    docsUrl: 'https://www.perplexity.ai/settings/api',
    category: 'tools',
  },
  tavily: {
    label: 'Tavily',
    description: 'Web search API optimized for AI agents',
    placeholder: 'tvly-...',
    docsUrl: 'https://app.tavily.com/home',
    category: 'tools',
  },
  e2b: {
    label: 'E2B',
    description: 'Secure code execution in Firecracker microVMs',
    placeholder: 'e2b_...',
    docsUrl: 'https://e2b.dev/dashboard',
    category: 'tools',
  },
} as const;

type ProviderKey = keyof typeof PROVIDERS;

const schema = z.object({
  name: z.string().min(1, 'Name is required'),
  provider: z.enum(['openrouter', 'openai', 'anthropic', 'google', 'parallelai', 'exa', 'perplexity', 'tavily', 'e2b'] as const),
  api_key: z.string().min(1, 'API key is required'),
  is_default: z.boolean(),
});

interface APIKeyFormProps {
    onClose: () => void;
}

export function APIKeyForm({ onClose }: APIKeyFormProps) {
  const createKey = useCreateAPIKey();
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const form = useForm<z.infer<typeof schema>>({
      resolver: zodResolver(schema),
      defaultValues: {
          name: '',
          provider: 'openrouter',
          api_key: '',
          is_default: true
        }
    });

  const selectedProvider = form.watch('provider') as ProviderKey;
  const providerConfig = PROVIDERS[selectedProvider];

  const onSubmit = async (data: z.infer<typeof schema>) => {
    // Prevent double submission
    if (isSubmitting || createKey.isPending) {
      return;
    }

    setIsSubmitting(true);
    setError(null);

    try {
        await createKey.mutateAsync(data);
        onClose();
    } catch (err) {
        console.error("Failed to create API key", err);

        // Handle specific error cases
        if (err instanceof APIError) {
          if (err.status === 409) {
            setError(`An API key with this name already exists for ${providerConfig.label}. Please use a different name.`);
          } else {
            setError(err.message || 'Failed to create API key. Please try again.');
          }
        } else {
          setError('An unexpected error occurred. Please try again.');
        }
    } finally {
        setIsSubmitting(false);
    }
  };

  // Group providers by category
  const llmProviders = Object.entries(PROVIDERS).filter(([, v]) => v.category === 'llm');
  const toolProviders = Object.entries(PROVIDERS).filter(([, v]) => v.category === 'tools');

  return (
    <Dialog open onOpenChange={onClose}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>Add API Key</DialogTitle>
          <DialogDescription>
            Configure API keys for LLM providers and workflow tools
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="provider"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Provider</FormLabel>
                  <Select onValueChange={field.onChange} defaultValue={field.value}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Select a provider" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">LLM Providers</div>
                      {llmProviders.map(([key, config]) => (
                        <SelectItem key={key} value={key}>
                          {config.label}
                        </SelectItem>
                      ))}
                      <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground mt-1">Workflow Tools</div>
                      {toolProviders.map(([key, config]) => (
                        <SelectItem key={key} value={key}>
                          {config.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormDescription className="text-xs">
                    {providerConfig.description}{' '}
                    <a
                      href={providerConfig.docsUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary hover:underline"
                    >
                      Get API key
                    </a>
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input placeholder={`My ${providerConfig.label} Key`} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="api_key"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>API Key</FormLabel>
                  <FormControl>
                    <Input
                      type="password"
                      placeholder={providerConfig.placeholder}
                      {...field}
                      autoComplete="off"
                    />
                  </FormControl>
                  <FormDescription className="text-xs">
                    Your key is encrypted and stored securely
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="is_default"
              render={({ field }) => (
                <FormItem className="flex items-center justify-between rounded-lg border p-3">
                  <div className="space-y-0.5">
                    <FormLabel className="text-sm">Set as default</FormLabel>
                    <FormDescription className="text-xs">
                      Use this key when {providerConfig.label} is needed
                    </FormDescription>
                  </div>
                  <FormControl>
                    <Switch checked={field.value} onCheckedChange={field.onChange} />
                  </FormControl>
                </FormItem>
              )}
            />
            {error && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <div className="flex justify-end gap-2 pt-2">
                <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting}>Cancel</Button>
                <Button type="submit" disabled={isSubmitting || createKey.isPending}>
                    {isSubmitting || createKey.isPending ? "Saving..." : "Save API Key"}
                </Button>
            </div>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
