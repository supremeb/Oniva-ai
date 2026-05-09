'use client';

import { useState, useCallback, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import {
  DndContext,
  DragEndEvent,
  DragOverlay,
  DragStartEvent,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  closestCenter,
} from '@dnd-kit/core';
import {
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
  useSortable,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import {
  Loader2,
  GripVertical,
  Trash2,
  Settings2,
  Zap,
  CheckCircle2,
  XCircle,
  Server,
} from 'lucide-react';
import {
  OpenAiLogo,
  Brain,
  Shuffle,
  Cloud,
  GoogleLogo,
  GearSix,
} from '@phosphor-icons/react';
import type { Icon as PhosphorIcon } from '@phosphor-icons/react';
import {
  useLLMProviders,
  useDeleteLLMProvider,
  useUpdateLLMProvider,
  useReorderLLMProviders,
} from '@/hooks/useLLMProviders';
import { toast } from 'sonner';
import type { LLMProvider } from '@/lib/api/llm-providers';
import { cn } from '@/lib/utils';

// Provider type icons and colors
const PROVIDER_CONFIG: Record<string, { Icon: PhosphorIcon; color: string; label: string }> = {
  openai: { Icon: OpenAiLogo, color: 'bg-green-500/10 text-green-500', label: 'OpenAI' },
  anthropic: { Icon: Brain, color: 'bg-orange-500/10 text-orange-500', label: 'Anthropic' },
  openrouter: { Icon: Shuffle, color: 'bg-purple-500/10 text-purple-500', label: 'OpenRouter' },
  azure: { Icon: Cloud, color: 'bg-blue-500/10 text-blue-500', label: 'Azure OpenAI' },
  google: { Icon: GoogleLogo, color: 'bg-yellow-500/10 text-yellow-500', label: 'Google AI' },
  custom: { Icon: GearSix, color: 'bg-gray-500/10 text-gray-500', label: 'Custom' },
};

interface SortableProviderCardProps {
  provider: LLMProvider;
  onDelete: (id: string) => void;
  onToggle: (id: string, enabled: boolean) => void;
  isDeleting: boolean;
}

function SortableProviderCard({
  provider,
  onDelete,
  onToggle,
  isDeleting,
}: SortableProviderCardProps) {
  const router = useRouter();
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: provider.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const config = PROVIDER_CONFIG[provider.provider_type] || PROVIDER_CONFIG.custom;

  return (
    <Card
      ref={setNodeRef}
      style={style}
      className={cn(
        'transition-all duration-200',
        isDragging && 'opacity-50 shadow-lg scale-105',
        !provider.is_enabled && 'opacity-60'
      )}
    >
      <CardContent className="flex items-center gap-3 p-4">
        {/* Drag Handle */}
        <button
          {...attributes}
          {...listeners}
          className="cursor-grab active:cursor-grabbing p-1 -ml-1 text-muted-foreground hover:text-foreground"
        >
          <GripVertical className="h-4 w-4" />
        </button>

        {/* Provider Icon */}
        <div className={cn('p-2 rounded-lg', config.color)}>
          <config.Icon className="h-5 w-5" weight="regular" />
        </div>

        {/* Provider Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <p className="font-medium truncate">{provider.display_name}</p>
            {provider.is_default && (
              <Badge variant="secondary" className="text-xs">Default</Badge>
            )}
            {provider.priority === 0 && (
              <Badge variant="outline" className="text-xs">Primary</Badge>
            )}
          </div>
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span>{config.label}</span>
            {provider.api_base && (
              <>
                <span>•</span>
                <span className="truncate max-w-[200px]">{provider.api_base}</span>
              </>
            )}
            {provider.has_api_key ? (
              <CheckCircle2 className="h-3 w-3 text-green-500" />
            ) : (
              <XCircle className="h-3 w-3 text-red-500" />
            )}
          </div>
          {provider.models.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1">
              {provider.models.slice(0, 3).map((model) => (
                <Badge key={model} variant="outline" className="text-xs">
                  {model}
                </Badge>
              ))}
              {provider.models.length > 3 && (
                <Badge variant="outline" className="text-xs">
                  +{provider.models.length - 3} more
                </Badge>
              )}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2">
          <Switch
            checked={provider.is_enabled}
            onCheckedChange={(checked) => onToggle(provider.id, checked)}
          />
          <Button
            variant="ghost"
            size="icon"
            onClick={() => router.push(`/settings/llm-providers/${provider.id}`)}
            className="text-muted-foreground hover:text-foreground"
            aria-label={`Configure ${provider.display_name}`}
          >
            <Settings2 className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onDelete(provider.id)}
            disabled={isDeleting}
            className="text-muted-foreground hover:text-destructive"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ProviderCardOverlay({ provider }: { provider: LLMProvider }) {
  const config = PROVIDER_CONFIG[provider.provider_type] || PROVIDER_CONFIG.custom;

  return (
    <Card className="shadow-lg">
      <CardContent className="flex items-center gap-3 p-4">
        <div className="p-1 text-muted-foreground">
          <GripVertical className="h-4 w-4" />
        </div>
        <div className={cn('p-2 rounded-lg', config.color)}>
          <config.Icon className="h-5 w-5" weight="regular" />
        </div>
        <div className="flex-1">
          <p className="font-medium">{provider.display_name}</p>
          <p className="text-sm text-muted-foreground">{config.label}</p>
        </div>
      </CardContent>
    </Card>
  );
}

export function LLMProvidersSettings() {
  const { data: providers = [], isLoading, error } = useLLMProviders();
  const deleteProvider = useDeleteLLMProvider();
  const updateProvider = useUpdateLLMProvider();
  const reorderProviders = useReorderLLMProviders();

  const [activeId, setActiveId] = useState<string | null>(null);

  // Get provider IDs for sortable context
  const providerIds = useMemo(() => providers.map((p) => p.id), [providers]);

  // Find actively dragged provider
  const activeProvider = useMemo(
    () => providers.find((p) => p.id === activeId),
    [providers, activeId]
  );

  // Configure sensors for drag interactions
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  const handleDragStart = useCallback((event: DragStartEvent) => {
    setActiveId(event.active.id as string);
  }, []);

  const handleDragEnd = useCallback(
    async (event: DragEndEvent) => {
      const { active, over } = event;
      setActiveId(null);

      if (!over || active.id === over.id) {
        return;
      }

      const oldIndex = providerIds.indexOf(active.id as string);
      const newIndex = providerIds.indexOf(over.id as string);

      if (oldIndex === -1 || newIndex === -1) {
        return;
      }

      // Create new order
      const newOrder = [...providerIds];
      newOrder.splice(oldIndex, 1);
      newOrder.splice(newIndex, 0, active.id as string);

      try {
        await reorderProviders.mutateAsync(newOrder);
        toast('Providers reordered', { description: 'Failover priority has been updated.' });
      } catch {
        toast.error('Error', { description: 'Failed to reorder providers.' });
      }
    },
    [providerIds, reorderProviders]
  );

  const handleDelete = async (id: string) => {
    try {
      await deleteProvider.mutateAsync(id);
      toast('Provider deleted', { description: 'The LLM provider has been removed.' });
    } catch {
      toast.error('Error', { description: 'Failed to delete provider.' });
    }
  };

  const handleToggle = async (id: string, enabled: boolean) => {
    try {
      await updateProvider.mutateAsync({ id, data: { is_enabled: enabled } });
      toast(enabled ? 'Provider enabled' : 'Provider disabled', {
        description: enabled
          ? 'The provider will be used for LLM requests.'
          : 'The provider will be skipped in the failover chain.',
      });
    } catch {
      toast.error('Error', { description: 'Failed to update provider.' });
    }
  };

  if (isLoading) {
    return (
      <div className="flex justify-center p-8">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error) {
    return <div className="text-destructive p-4">Error loading LLM providers.</div>;
  }

  return (
    <div className="space-y-4">
      {providers.length === 0 ? (
        <div className="text-center p-8 border border-dashed rounded-lg">
          <Server className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
          <h3 className="text-lg font-medium mb-2">No LLM providers configured</h3>
          <p className="text-muted-foreground mb-4">
            Add your first LLM provider to enable AI-powered workflows.
          </p>
        </div>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            items={providerIds}
            strategy={verticalListSortingStrategy}
          >
            <div className="space-y-2">
              {providers.map((provider) => (
                <SortableProviderCard
                  key={provider.id}
                  provider={provider}
                  onDelete={handleDelete}
                  onToggle={handleToggle}
                  isDeleting={deleteProvider.isPending}
                />
              ))}
            </div>
          </SortableContext>

          <DragOverlay>
            {activeProvider && <ProviderCardOverlay provider={activeProvider} />}
          </DragOverlay>
        </DndContext>
      )}

      {/* Failover Info */}
      {providers.length > 1 && (
        <div className="flex items-start gap-2 p-3 bg-muted/50 rounded-lg text-sm">
          <Zap className="h-4 w-4 mt-0.5 text-yellow-500" />
          <div>
            <p className="font-medium">Automatic Failover</p>
            <p className="text-muted-foreground">
              If the primary provider fails, requests will automatically fall back to the
              next enabled provider in order.
            </p>
          </div>
        </div>
      )}

    </div>
  );
}
