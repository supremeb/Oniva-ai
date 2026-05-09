'use client';

import { ReactNode, useState } from 'react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Slider } from '@/components/ui/slider';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Button } from '@/components/ui/button';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import {
    Brain,
    Lightning,
    TreeStructure,
    Plus,
    Trash,
    ArrowsLeftRight,
    ArrowDown,
    Warning,
    ChartLineUp,
    IdentificationCard,
} from '@phosphor-icons/react';
import { SimpleCollapsibleSection } from './CollapsibleSection';
import { cn } from '@/lib/utils';
import { useAvailableModels } from '@/hooks/useLLMProviders';
import type { DeepWorkModels } from '@/lib/api/agents';

interface ChildAgentConfig {
    agent_id: string;
    task: string;
    order: number;
}

interface ResourceConstraints {
    max_concurrent?: number;
    token_budget?: number;
    timeout_seconds?: number;
    max_depth?: number;
}

interface SettingsTabProps {
    // Identity
    name: string;
    description: string;
    setName: (value: string) => void;
    setDescription: (value: string) => void;

    // Model
    model: string;
    temperature: number;
    maxTokens: number;
    setModel: (value: string) => void;
    setTemperature: (value: number) => void;
    setMaxTokens: (value: number) => void;

    // Deep Work Models
    deepWorkModels?: DeepWorkModels;
    onDeepWorkModelsChange?: (models: DeepWorkModels) => void;

    // Orchestration
    isSupervisor?: boolean;
    executionMode?: 'sequential' | 'parallel';
    childAgentConfig?: ChildAgentConfig[];
    resourceConstraints?: ResourceConstraints;
    availableAgents?: Array<{ id: string; name: string }>;
    onIsSupervisorChange?: (value: boolean) => void;
    onExecutionModeChange?: (mode: 'sequential' | 'parallel') => void;
    onChildAgentConfigChange?: (config: ChildAgentConfig[]) => void;
    onResourceConstraintsChange?: (constraints: ResourceConstraints) => void;

    // Versions panel (rendered inline)
    versionsContent?: ReactNode;

    className?: string;
}

export function SettingsTab({
    name,
    description,
    setName,
    setDescription,
    model,
    temperature,
    maxTokens,
    setModel,
    setTemperature,
    setMaxTokens,
    deepWorkModels = {},
    onDeepWorkModelsChange,
    isSupervisor = false,
    executionMode = 'sequential',
    childAgentConfig = [],
    resourceConstraints = {},
    availableAgents = [],
    onIsSupervisorChange,
    onExecutionModeChange,
    onChildAgentConfigChange,
    onResourceConstraintsChange,
    versionsContent,
    className,
}: SettingsTabProps) {
    const { models: availableModels } = useAvailableModels();

    // Orchestration handlers
    const handleAddChild = () => {
        if (!onChildAgentConfigChange) return;
        const nextOrder = childAgentConfig.length;
        onChildAgentConfigChange([
            ...childAgentConfig,
            { agent_id: '', task: '', order: nextOrder },
        ]);
    };

    const handleRemoveChild = (index: number) => {
        if (!onChildAgentConfigChange) return;
        const updated = childAgentConfig.filter((_, i) => i !== index);
        onChildAgentConfigChange(updated.map((c, i) => ({ ...c, order: i })));
    };

    const handleUpdateChild = (index: number, field: keyof ChildAgentConfig, value: string | number) => {
        if (!onChildAgentConfigChange) return;
        const updated = childAgentConfig.map((c, i) =>
            i === index ? { ...c, [field]: value } : c
        );
        onChildAgentConfigChange(updated);
    };

    const handleConstraintChange = (field: keyof ResourceConstraints, value: number) => {
        if (!onResourceConstraintsChange) return;
        onResourceConstraintsChange({ ...resourceConstraints, [field]: value });
    };

    return (
        <ScrollArea className={cn('h-full', className)}>
            <div className="p-3 space-y-3">
                {/* Identity */}
                <SimpleCollapsibleSection
                    title="Identity"
                    icon={<IdentificationCard className="h-4 w-4" weight="duotone" />}
                    defaultOpen={false}
                >
                    <div className="space-y-3">
                        <div className="space-y-1.5">
                            <Label htmlFor="settings-name" className="text-xs font-medium leading-none">
                                Name <span className="text-destructive">*</span>
                            </Label>
                            <Input
                                id="settings-name"
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                                placeholder="My Custom Agent"
                                className="h-8"
                            />
                        </div>
                        <div className="space-y-1.5">
                            <Label htmlFor="settings-description" className="text-xs font-medium leading-none">
                                Description
                            </Label>
                            <Input
                                id="settings-description"
                                value={description}
                                onChange={(e) => setDescription(e.target.value)}
                                placeholder="What does this agent do?"
                                className="h-8"
                            />
                        </div>
                    </div>
                </SimpleCollapsibleSection>

                {/* Model */}
                <SimpleCollapsibleSection
                    title="Model"
                    icon={<Brain className="h-4 w-4" weight="duotone" />}
                    defaultOpen={true}
                >
                    <div className="space-y-3">
                        <div className="space-y-1.5">
                            <Label htmlFor="settings-model" className="text-xs font-medium leading-none">Model</Label>
                            <Select value={model} onValueChange={setModel}>
                                <SelectTrigger className="h-8">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {availableModels.map((m) => (
                                        <SelectItem key={m.id} value={m.id}>
                                            {m.name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1.5">
                                <Label className="text-xs font-medium leading-none">
                                    Temperature: {temperature.toFixed(1)}
                                </Label>
                                <Slider
                                    value={[temperature]}
                                    onValueChange={([value]) => setTemperature(value)}
                                    min={0}
                                    max={2}
                                    step={0.1}
                                    className="py-2"
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="settings-maxTokens" className="text-xs font-medium leading-none">
                                    Max Tokens
                                </Label>
                                <Input
                                    id="settings-maxTokens"
                                    type="number"
                                    value={maxTokens}
                                    onChange={(e) => setMaxTokens(parseInt(e.target.value) || 2000)}
                                    min={1}
                                    max={32000}
                                    className="h-8"
                                />
                            </div>
                        </div>
                    </div>
                </SimpleCollapsibleSection>

                {/* Deep Work Models */}
                {onDeepWorkModelsChange && (
                    <SimpleCollapsibleSection
                        title="Deep Work Models"
                        icon={<Lightning className="h-4 w-4" weight="duotone" />}
                        defaultOpen={false}
                    >
                        <p className="text-[11px] leading-4 text-muted-foreground mb-3">
                            Override models for each deep work role. Leave blank to auto-detect.
                        </p>
                        <div className="space-y-2.5">
                            {(['high', 'mid', 'low'] as const).map((tier) => (
                                <div key={tier} className="space-y-1">
                                    <Label className="text-xs font-medium leading-none">
                                        {tier === 'high' ? 'Orchestrator & Synthesizer' : tier === 'mid' ? 'Workers & Reflection' : 'Extractor'} ({tier})
                                    </Label>
                                    <Select
                                        value={deepWorkModels[tier] || ''}
                                        onValueChange={(v) => onDeepWorkModelsChange({ ...deepWorkModels, [tier]: v || undefined })}
                                    >
                                        <SelectTrigger className="h-8 text-xs">
                                            <SelectValue placeholder="Auto-detect from primary model" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {availableModels.map((m) => (
                                                <SelectItem key={m.id} value={m.id}>
                                                    {m.name}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                            ))}
                        </div>
                    </SimpleCollapsibleSection>
                )}

                {/* Orchestrate */}
                {onIsSupervisorChange && (
                    <SimpleCollapsibleSection
                        title="Orchestrate"
                        icon={<TreeStructure className="h-4 w-4" weight="duotone" />}
                        badge={
                            isSupervisor ? (
                                <Badge variant="secondary" className="text-xs ml-2">
                                    {childAgentConfig.length} child{childAgentConfig.length !== 1 ? 'ren' : ''}
                                </Badge>
                            ) : undefined
                        }
                        defaultOpen={false}
                    >
                        <div className="space-y-3">
                            {/* Supervisor Toggle */}
                            <div className="flex items-center justify-between gap-3">
                                <div className="min-w-0">
                                    <Label className="text-xs font-medium leading-none">Supervisor Mode</Label>
                                    <p className="text-[11px] leading-4 text-muted-foreground mt-1">
                                        Enable this agent to coordinate child agents
                                    </p>
                                </div>
                                <button
                                    onClick={() => onIsSupervisorChange(!isSupervisor)}
                                    className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${
                                        isSupervisor ? 'bg-primary' : 'bg-muted'
                                    }`}
                                >
                                    <span
                                        className={`pointer-events-none block h-4 w-4 rounded-full bg-background shadow-lg ring-0 transition-transform ${
                                            isSupervisor ? 'translate-x-4' : 'translate-x-0'
                                        }`}
                                    />
                                </button>
                            </div>

                            {isSupervisor && (
                                <>
                                    {/* Execution Mode */}
                                    <div className="space-y-1.5">
                                        <Label className="text-xs font-medium leading-none">Execution Mode</Label>
                                        <div className="grid grid-cols-2 gap-2">
                                            <button
                                                onClick={() => onExecutionModeChange?.('sequential')}
                                                className={`p-3 rounded-lg border text-left transition-colors ${
                                                    executionMode === 'sequential'
                                                        ? 'border-primary bg-primary/5'
                                                        : 'border-border hover:border-foreground/20'
                                                }`}
                                            >
                                                <ArrowDown className="h-4 w-4 mb-1 text-muted-foreground" />
                                                <div className="text-xs font-medium">Sequential</div>
                                            </button>
                                            <button
                                                onClick={() => onExecutionModeChange?.('parallel')}
                                                className={`p-3 rounded-lg border text-left transition-colors ${
                                                    executionMode === 'parallel'
                                                        ? 'border-primary bg-primary/5'
                                                        : 'border-border hover:border-foreground/20'
                                                }`}
                                            >
                                                <ArrowsLeftRight className="h-4 w-4 mb-1 text-muted-foreground" />
                                                <div className="text-xs font-medium">Parallel</div>
                                            </button>
                                        </div>
                                    </div>

                                    {/* Child Agents */}
                                    <div className="space-y-1.5">
                                        <div className="flex items-center justify-between">
                                            <Label className="text-xs font-medium leading-none">
                                                Child Agents ({childAgentConfig.length})
                                            </Label>
                                            <button
                                                onClick={handleAddChild}
                                                className="flex items-center gap-1 text-xs text-primary hover:text-primary/80 transition-colors"
                                            >
                                                <Plus className="h-3 w-3" />
                                                Add
                                            </button>
                                        </div>

                                        {childAgentConfig.length === 0 ? (
                                            <div className="text-center py-6 border border-dashed border-border rounded-lg">
                                                <TreeStructure className="h-6 w-6 text-muted-foreground mx-auto mb-2" weight="light" />
                                                <p className="text-xs text-muted-foreground">No child agents configured</p>
                                            </div>
                                        ) : (
                                            <div className="space-y-2">
                                                {childAgentConfig.map((child, index) => (
                                                    <div key={index} className="p-3 rounded-lg border border-border space-y-2">
                                                        <div className="flex items-center justify-between">
                                                            <Badge variant="outline" className="text-[10px] py-0">#{index + 1}</Badge>
                                                            <button
                                                                onClick={() => handleRemoveChild(index)}
                                                                className="p-1 hover:bg-muted rounded transition-colors"
                                                            >
                                                                <Trash className="h-3 w-3 text-muted-foreground hover:text-destructive" />
                                                            </button>
                                                        </div>
                                                        <select
                                                            value={child.agent_id}
                                                            onChange={(e) => handleUpdateChild(index, 'agent_id', e.target.value)}
                                                            className="w-full h-8 px-2 text-xs border border-border rounded-md bg-background"
                                                        >
                                                            <option value="">Select agent...</option>
                                                            {availableAgents.map((a) => (
                                                                <option key={a.id} value={a.id}>{a.name}</option>
                                                            ))}
                                                        </select>
                                                        <Input
                                                            placeholder="Task description..."
                                                            value={child.task}
                                                            onChange={(e) => handleUpdateChild(index, 'task', e.target.value)}
                                                            className="h-8 text-xs"
                                                        />
                                                    </div>
                                                ))}
                                            </div>
                                        )}
                                    </div>

                                    {/* Resource Constraints */}
                                    <div className="space-y-2">
                                        <Label className="text-xs font-medium leading-none">Resource Constraints</Label>
                                        <div className="grid grid-cols-2 gap-2.5">
                                            <div className="space-y-1">
                                                <label className="text-[10px] leading-none text-muted-foreground uppercase tracking-wider">Max Concurrent</label>
                                                <Input
                                                    type="number" min={1} max={10}
                                                    value={resourceConstraints.max_concurrent ?? 3}
                                                    onChange={(e) => handleConstraintChange('max_concurrent', parseInt(e.target.value) || 3)}
                                                    className="h-8 text-xs"
                                                />
                                            </div>
                                            <div className="space-y-1">
                                                <label className="text-[10px] leading-none text-muted-foreground uppercase tracking-wider">Token Budget</label>
                                                <Input
                                                    type="number" min={1000} step={10000}
                                                    value={resourceConstraints.token_budget ?? 100000}
                                                    onChange={(e) => handleConstraintChange('token_budget', parseInt(e.target.value) || 100000)}
                                                    className="h-8 text-xs"
                                                />
                                            </div>
                                            <div className="space-y-1">
                                                <label className="text-[10px] leading-none text-muted-foreground uppercase tracking-wider">Timeout (s)</label>
                                                <Input
                                                    type="number" min={30} max={3600}
                                                    value={resourceConstraints.timeout_seconds ?? 300}
                                                    onChange={(e) => handleConstraintChange('timeout_seconds', parseInt(e.target.value) || 300)}
                                                    className="h-8 text-xs"
                                                />
                                            </div>
                                            <div className="space-y-1">
                                                <label className="text-[10px] leading-none text-muted-foreground uppercase tracking-wider">Max Depth</label>
                                                <Input
                                                    type="number" min={1} max={5}
                                                    value={resourceConstraints.max_depth ?? 3}
                                                    onChange={(e) => handleConstraintChange('max_depth', parseInt(e.target.value) || 3)}
                                                    className="h-8 text-xs"
                                                />
                                            </div>
                                        </div>
                                        {executionMode === 'parallel' && (resourceConstraints.max_concurrent ?? 3) > 5 && (
                                            <div className="flex items-start gap-2 p-2 rounded-md bg-yellow-50 dark:bg-yellow-950/20 text-[11px] leading-4">
                                                <Warning className="h-3.5 w-3.5 text-yellow-600 dark:text-yellow-500 mt-px shrink-0" />
                                                <span className="text-yellow-700 dark:text-yellow-500">
                                                    High concurrency may increase cost and latency.
                                                </span>
                                            </div>
                                        )}
                                    </div>
                                </>
                            )}
                        </div>
                    </SimpleCollapsibleSection>
                )}

                {/* Versions */}
                {versionsContent && (
                    <SimpleCollapsibleSection
                        title="Versions"
                        icon={<ChartLineUp className="h-4 w-4" weight="duotone" />}
                        defaultOpen={false}
                    >
                        {versionsContent}
                    </SimpleCollapsibleSection>
                )}
            </div>
        </ScrollArea>
    );
}
