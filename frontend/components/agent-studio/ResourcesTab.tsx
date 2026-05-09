'use client';

import { useState, useMemo } from 'react';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    Sparkle,
    Plugs,
    BookOpenText,
    TreeStructure,
    CircleNotch,
    CheckCircle,
    MagnifyingGlass,
    Warning,
} from '@phosphor-icons/react';
import { SimpleCollapsibleSection } from './CollapsibleSection';
import { cn } from '@/lib/utils';
import { EnableConnectorDialog } from '@/components/marketplace/EnableConnectorDialog';
import { useConnectors, useEnabledConnectors, useConnectorDetail, useEnableConnector } from '@/hooks/useMarketplace';
import type { MarketplaceConnector, EnabledConnector } from '@/lib/api/marketplace';
import { toast } from 'sonner';
import { useSkills } from '@/hooks/useSkills';
import { useKnowledgeBases } from '@/hooks/useKnowledgeBases';
import { useWorkflows } from '@/hooks/useWorkflow';
import type { Workflow } from '@/types/api';

interface ResourcesTabProps {
    selectedSkills: string[];
    selectedConnectors: string[];
    knowledgeBaseIds: string[];
    workflowIds: string[];
    toggleSkill: (skill: string) => void;
    toggleConnector: (connector: string) => void;
    toggleKnowledgeBase: (kbId: string) => void;
    toggleWorkflow: (id: string) => void;
    className?: string;
}

export function ResourcesTab({
    selectedSkills,
    selectedConnectors,
    knowledgeBaseIds,
    workflowIds,
    toggleSkill,
    toggleConnector,
    toggleKnowledgeBase,
    toggleWorkflow,
    className,
}: ResourcesTabProps) {
    const { data: availableSkills = [], isLoading: skillsLoading } = useSkills();
    const { data: knowledgeBases = [], isLoading: kbLoading } = useKnowledgeBases();
    const { data: workflows = [], isLoading: workflowsLoading } = useWorkflows();

    const [skillSearch, setSkillSearch] = useState('');
    const [connectorToEnable, setConnectorToEnable] = useState<string | null>(null);

    // Load marketplace connectors
    const { data: marketplaceConnectors = [], isLoading: connectorsLoading } = useConnectors();
    const { data: enabledData } = useEnabledConnectors();
    const { data: connectorDetail, isLoading: detailLoading } = useConnectorDetail(connectorToEnable ?? undefined);
    const enableMutation = useEnableConnector();

    const enabledConnectorIds = useMemo(() => {
        return new Set(enabledData?.connectors?.map((c: EnabledConnector) => c.connector_id) || []);
    }, [enabledData]);

    const availableConnectors = useMemo(() => {
        return marketplaceConnectors.map((c: MarketplaceConnector) => ({
            ...c,
            is_enabled: enabledConnectorIds.has(c.id) || c.is_enabled,
        }));
    }, [marketplaceConnectors, enabledConnectorIds]);

    const filteredSkills = useMemo(() => {
        if (!skillSearch) return availableSkills;
        return availableSkills.filter(s =>
            s.name.toLowerCase().includes(skillSearch.toLowerCase())
        );
    }, [availableSkills, skillSearch]);

    const handleEnableConnector = async (credentials: Record<string, string>) => {
        if (!connectorToEnable) return;
        try {
            const result = await enableMutation.mutateAsync({
                connectorId: connectorToEnable,
                request: { credentials },
            });
            toast.success(result.message);
            setConnectorToEnable(null);
        } catch (error: unknown) {
            const errorMessage = error instanceof Error ? error.message : 'Failed to enable connector';
            toast.error(errorMessage);
        }
    };

    return (
        <ScrollArea className={cn('h-full', className)}>
            <div className="p-3 space-y-3">
                {/* Skills */}
                <SimpleCollapsibleSection
                    title="Skills"
                    icon={<Sparkle className="h-4 w-4" weight="duotone" />}
                    badge={
                        <Badge variant="secondary" className="text-xs ml-2">
                            {selectedSkills.length}
                        </Badge>
                    }
                    defaultOpen={true}
                >
                    {skillsLoading ? (
                        <div className="flex items-center justify-center p-6">
                            <CircleNotch className="h-5 w-5 animate-spin text-muted-foreground" />
                        </div>
                    ) : (
                        <div className="space-y-2">
                            {availableSkills.length > 8 && (
                                <div className="relative">
                                    <MagnifyingGlass className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                                    <Input
                                        placeholder="Search skills..."
                                        value={skillSearch}
                                        onChange={(e) => setSkillSearch(e.target.value)}
                                        className="h-8 pl-8 text-sm"
                                    />
                                </div>
                            )}
                            <div className="grid grid-cols-2 gap-2">
                                {filteredSkills.map((skill) => (
                                    <div
                                        key={skill.name}
                                        className={cn(
                                            'flex items-center space-x-3 p-2.5 rounded-lg border transition-colors cursor-pointer',
                                            selectedSkills.includes(skill.name)
                                                ? 'bg-primary/5 border-primary/30'
                                                : 'hover:bg-accent/50'
                                        )}
                                        onClick={() => toggleSkill(skill.name)}
                                    >
                                        <Checkbox
                                            checked={selectedSkills.includes(skill.name)}
                                            onCheckedChange={() => toggleSkill(skill.name)}
                                        />
                                        <label className="flex-1 text-sm cursor-pointer truncate">
                                            <span className="font-medium">
                                                {skill.name.replace(/_/g, ' ')}
                                            </span>
                                        </label>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </SimpleCollapsibleSection>

                {/* Connectors */}
                <SimpleCollapsibleSection
                    title="Connectors"
                    icon={<Plugs className="h-4 w-4" weight="duotone" />}
                    badge={
                        <Badge variant="secondary" className="text-xs ml-2">
                            {selectedConnectors.length}
                        </Badge>
                    }
                    defaultOpen={false}
                >
                    {connectorsLoading ? (
                        <div className="flex items-center justify-center p-6">
                            <CircleNotch className="h-5 w-5 animate-spin text-muted-foreground" />
                        </div>
                    ) : availableConnectors.length > 0 ? (
                        <div className="space-y-2">
                            <p className="text-[11px] leading-4 text-muted-foreground mb-2.5">
                                Select connectors to enable for this agent.
                            </p>
                            <div className="grid grid-cols-1 gap-2 max-h-64 overflow-y-auto pr-1">
                                {availableConnectors.map((connector) => {
                                    const isEnabled = connector.is_enabled;
                                    const isSelected = selectedConnectors.includes(connector.id);

                                    return (
                                        <div
                                            key={connector.id}
                                            className={cn(
                                                'flex items-center gap-3 p-2.5 rounded-lg border transition-colors',
                                                isSelected && isEnabled
                                                    ? 'bg-primary/5 border-primary/30'
                                                    : isEnabled
                                                      ? 'hover:bg-accent/50 cursor-pointer'
                                                      : 'opacity-75'
                                            )}
                                            onClick={() => isEnabled && toggleConnector(connector.id)}
                                        >
                                            <Checkbox
                                                checked={isSelected}
                                                disabled={!isEnabled}
                                                onCheckedChange={() => isEnabled && toggleConnector(connector.id)}
                                                className={!isEnabled ? 'opacity-50' : ''}
                                            />
                                            <div className="flex-1 min-w-0">
                                                <div className="flex items-center gap-2">
                                                    <span className="text-sm font-medium truncate">
                                                        {connector.name}
                                                    </span>
                                                    {isEnabled ? (
                                                        <CheckCircle
                                                            className="h-3.5 w-3.5 text-green-600 shrink-0"
                                                            weight="fill"
                                                        />
                                                    ) : (
                                                        <Badge
                                                            variant="outline"
                                                            className="text-[10px] py-0 h-4 shrink-0 bg-amber-50 text-amber-700 border-amber-200"
                                                        >
                                                            Not configured
                                                        </Badge>
                                                    )}
                                                </div>
                                                <p className="text-xs text-muted-foreground truncate">
                                                    {connector.description}
                                                </p>
                                            </div>
                                            {!isEnabled && (
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    className="h-7 text-xs shrink-0"
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        setConnectorToEnable(connector.id);
                                                    }}
                                                >
                                                    Connect
                                                </Button>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    ) : (
                        <div className="text-center p-6 text-muted-foreground rounded-lg border border-dashed">
                            <Plugs className="h-8 w-8 mx-auto mb-2 opacity-40" />
                            <p className="text-sm">No connectors available</p>
                        </div>
                    )}
                </SimpleCollapsibleSection>

                {/* Knowledge Bases */}
                <SimpleCollapsibleSection
                    title="Knowledge"
                    icon={<BookOpenText className="h-4 w-4" weight="duotone" />}
                    badge={
                        knowledgeBaseIds.length > 0 ? (
                            <Badge variant="secondary" className="text-xs ml-2">
                                {knowledgeBaseIds.length}
                            </Badge>
                        ) : undefined
                    }
                    defaultOpen={false}
                >
                    {kbLoading ? (
                        <div className="flex items-center justify-center p-6">
                            <CircleNotch className="h-5 w-5 animate-spin text-muted-foreground" />
                        </div>
                    ) : knowledgeBases.length > 0 ? (
                        <div className="space-y-2">
                            <p className="text-[11px] leading-4 text-muted-foreground mb-2.5">
                                Selected knowledge bases provide RAG context during conversations.
                            </p>
                            {knowledgeBases.map((kb) => {
                                const isSelected = knowledgeBaseIds.includes(kb.id);
                                return (
                                    <div
                                        key={kb.id}
                                        className={cn(
                                            'flex items-center gap-3 p-2.5 rounded-lg border transition-colors cursor-pointer',
                                            isSelected
                                                ? 'bg-primary/5 border-primary/30'
                                                : 'hover:bg-accent/50'
                                        )}
                                        onClick={() => toggleKnowledgeBase(kb.id)}
                                    >
                                        <Checkbox
                                            checked={isSelected}
                                            onCheckedChange={() => toggleKnowledgeBase(kb.id)}
                                        />
                                        <div className="flex-1 min-w-0">
                                            <span className="text-sm font-medium truncate block">
                                                {kb.name}
                                            </span>
                                            {kb.description && (
                                                <p className="text-xs text-muted-foreground truncate">
                                                    {kb.description}
                                                </p>
                                            )}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    ) : (
                        <div className="text-center p-6 text-muted-foreground rounded-lg border border-dashed">
                            <BookOpenText className="h-8 w-8 mx-auto mb-2 opacity-40" />
                            <p className="text-sm">No knowledge bases</p>
                            <p className="text-xs mt-1">Upload documents in Settings to create knowledge bases</p>
                        </div>
                    )}
                </SimpleCollapsibleSection>

                {/* Workflows */}
                <SimpleCollapsibleSection
                    title="Workflows"
                    icon={<TreeStructure className="h-4 w-4" weight="duotone" />}
                    badge={
                        workflowIds.length > 0 ? (
                            <Badge variant="secondary" className="text-xs ml-2">
                                {workflowIds.length}
                            </Badge>
                        ) : undefined
                    }
                    defaultOpen={false}
                >
                    {workflowsLoading ? (
                        <div className="flex items-center justify-center p-6">
                            <CircleNotch className="h-5 w-5 animate-spin text-muted-foreground" />
                        </div>
                    ) : workflows.length > 0 ? (
                        <div className="space-y-2">
                            <p className="text-[11px] leading-4 text-muted-foreground mb-2.5">
                                Attach workflows to make them available as callable tools during conversations.
                            </p>
                            {workflowIds.length > 10 && (
                                <div className="flex items-center gap-2 p-2 rounded-lg bg-amber-50 border border-amber-200 text-amber-800 dark:bg-amber-950/30 dark:border-amber-800 dark:text-amber-300">
                                    <Warning className="h-4 w-4 shrink-0" weight="fill" />
                                    <p className="text-xs">Many attached workflows may reduce routing accuracy</p>
                                </div>
                            )}
                            {workflows.map((workflow: Workflow) => {
                                const isSelected = workflowIds.includes(workflow.id);
                                const nodeCount = workflow.spec?.nodes?.length ?? 0;
                                const description = workflow.description
                                    ? workflow.description.length > 60
                                        ? workflow.description.slice(0, 60) + '...'
                                        : workflow.description
                                    : null;
                                return (
                                    <div
                                        key={workflow.id}
                                        className={cn(
                                            'flex items-center gap-3 p-2.5 rounded-lg border transition-colors cursor-pointer',
                                            isSelected
                                                ? 'bg-primary/5 border-primary/30'
                                                : 'hover:bg-accent/50'
                                        )}
                                        onClick={() => toggleWorkflow(workflow.id)}
                                    >
                                        <Checkbox
                                            checked={isSelected}
                                            onCheckedChange={() => toggleWorkflow(workflow.id)}
                                        />
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2">
                                                <span className="text-sm font-medium truncate">
                                                    {workflow.name}
                                                </span>
                                                {nodeCount > 0 && (
                                                    <Badge
                                                        variant="outline"
                                                        className="text-[10px] py-0 h-4 shrink-0"
                                                    >
                                                        {nodeCount} {nodeCount === 1 ? 'node' : 'nodes'}
                                                    </Badge>
                                                )}
                                            </div>
                                            {description && (
                                                <p className="text-xs text-muted-foreground truncate">
                                                    {description}
                                                </p>
                                            )}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    ) : (
                        <div className="text-center p-6 text-muted-foreground rounded-lg border border-dashed">
                            <TreeStructure className="h-8 w-8 mx-auto mb-2 opacity-40" />
                            <p className="text-sm">No workflows available</p>
                            <p className="text-xs mt-1">Create one in the Workflow Builder</p>
                        </div>
                    )}
                </SimpleCollapsibleSection>

                {/* Enable Connector Dialog */}
                {connectorToEnable && connectorDetail && !detailLoading && (
                    <EnableConnectorDialog
                        connector={connectorDetail}
                        onClose={() => setConnectorToEnable(null)}
                        onEnable={handleEnableConnector}
                        isLoading={enableMutation.isPending}
                    />
                )}
            </div>
        </ScrollArea>
    );
}
