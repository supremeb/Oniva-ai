'use client';

import { Label } from '@/components/ui/label';
import { SUPPORTED_MODELS, type ModelDefinition } from '@/lib/models';
import { useAvailableModels } from '@/hooks/useLLMProviders';
import { cn } from '@/lib/utils';

export interface ModelSelectorProps {
    value: string;
    onChange: (modelId: string) => void;
    label?: string;
    placeholder?: string;
    className?: string;
    providers?: ModelDefinition['provider'][];
    showCapabilities?: boolean;
    disabled?: boolean;
    /** Override the model list instead of using the dynamic hook */
    models?: ModelDefinition[];
}

/**
 * Reusable model selector dropdown.
 * Shows models grouped by provider with optional capability badges.
 * Uses tenant-configured providers via useAvailableModels(), with
 * static SUPPORTED_MODELS as fallback.
 */
export function ModelSelector({
    value,
    onChange,
    label = 'Model',
    placeholder = 'Select a model...',
    className,
    providers,
    showCapabilities = false,
    disabled = false,
    models: modelsProp,
}: ModelSelectorProps) {
    const { models: dynamicModels } = useAvailableModels();

    // Use prop override, then dynamic, then static fallback
    const allModels = modelsProp ?? dynamicModels;

    // Filter models by provider if specified
    const models = providers
        ? allModels.filter((m) => providers.includes(m.provider))
        : allModels;

    // Group models by provider
    const groupedModels = models.reduce((acc, model) => {
        if (!acc[model.provider]) {
            acc[model.provider] = [];
        }
        acc[model.provider].push(model);
        return acc;
    }, {} as Record<string, ModelDefinition[]>);

    return (
        <div className="space-y-1.5">
            {label && <Label className="text-xs">{label}</Label>}
            <select
                className={cn(
                    'w-full h-8 rounded-md border px-2 text-xs bg-background',
                    'focus:ring-2 focus:ring-primary/20 focus:border-primary transition-colors',
                    className
                )}
                value={value}
                onChange={(e) => onChange(e.target.value)}
                disabled={disabled}
            >
                <option value="">{placeholder}</option>
                {Object.entries(groupedModels).map(([provider, providerModels]) => (
                    <optgroup key={provider} label={provider}>
                        {providerModels.map((model) => (
                            <option key={model.id} value={model.id}>
                                {model.name}
                                {showCapabilities && model.capabilities.includes('vision') && ' 👁️'}
                            </option>
                        ))}
                    </optgroup>
                ))}
            </select>
        </div>
    );
}

/**
 * Simple inline model selector (no label wrapper)
 */
export function ModelSelectorInline({
    value,
    onChange,
    placeholder = 'Select a model...',
    className,
    providers,
    disabled = false,
    models: modelsProp,
}: Omit<ModelSelectorProps, 'label' | 'showCapabilities'>) {
    const { models: dynamicModels } = useAvailableModels();
    const allModels = modelsProp ?? dynamicModels;

    const models = providers
        ? allModels.filter((m) => providers.includes(m.provider))
        : allModels;

    return (
        <select
            className={cn(
                'w-full h-8 rounded-md border px-2 text-xs bg-background',
                'focus:ring-2 focus:ring-primary/20 focus:border-primary transition-colors',
                className
            )}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            disabled={disabled}
        >
            <option value="">{placeholder}</option>
            {models.map((model) => (
                <option key={model.id} value={model.id}>
                    {model.name} ({model.provider})
                </option>
            ))}
        </select>
    );
}
