'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import {
    ArrowLeft,
    Robot,
    CircleNotch,
    FloppyDisk,
    CheckCircle,
    Play,
    Globe,
    CloudCheck,
    CloudSlash,
    CloudArrowUp,
    WarningCircle,
} from '@phosphor-icons/react';
import {
    getCustomAgent,
    useCustomAgents,
    getMigratedAgentId,
    seedExampleAgents,
    updateCustomAgent,
    type CustomAgent,
} from '@/lib/api/agents';
import { toast } from 'sonner';
import { useNavigationGuard } from '@/hooks/useNavigationGuard';
import PublishAgentDialog from '@/components/agents/PublishAgentDialog';
import { useAgentForm } from '@/hooks/useAgentForm';
import {
    AgentStudioChatPanel,
    TestCasesPanel,
    VersionsPanel,
    InstructionsEditor,
} from '@/components/agent-studio';
import { V2Layout } from '@/components/agent-studio/V2Layout';
import { ResourcesTab } from '@/components/agent-studio/ResourcesTab';
import { SettingsTab } from '@/components/agent-studio/SettingsTab';
import { BackgroundTab } from '@/components/agent-studio/BackgroundTab';
import { runEvaluation } from '@/lib/api/agent-studio';
import { BUILTIN_AGENT_UUIDS } from '@/lib/builtin-agents';

type SaveStatus = 'saved' | 'saving' | 'unsaved';
const AUTOSAVE_DEBOUNCE = 2000;

/**
 * Error boundary for Agent Studio — catches malformed agent config
 * or unexpected rendering errors and shows recovery UI.
 */
interface ErrorBoundaryState {
    hasError: boolean;
    error: Error | null;
}

class AgentStudioErrorBoundary extends React.Component<
    { children: React.ReactNode },
    ErrorBoundaryState
> {
    constructor(props: { children: React.ReactNode }) {
        super(props);
        this.state = { hasError: false, error: null };
    }

    static getDerivedStateFromError(error: Error): ErrorBoundaryState {
        return { hasError: true, error };
    }

    componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
        console.error('[AgentStudio] Uncaught error:', error, errorInfo);
    }

    render() {
        if (this.state.hasError) {
            return (
                <div className="flex items-center justify-center h-full min-h-[400px]">
                    <div className="text-center space-y-4 max-w-md px-6">
                        <WarningCircle className="h-12 w-12 mx-auto text-destructive" weight="duotone" />
                        <h2 className="text-lg font-semibold">Something went wrong</h2>
                        <p className="text-sm text-muted-foreground">
                            The Agent Studio encountered an unexpected error.
                            This may be caused by a malformed agent configuration.
                        </p>
                        {this.state.error && (
                            <pre className="text-xs text-left bg-muted rounded-md p-3 overflow-auto max-h-32">
                                {this.state.error.message}
                            </pre>
                        )}
                        <Button
                            onClick={() => window.location.reload()}
                            className="gap-2"
                        >
                            <ArrowLeft className="h-4 w-4" />
                            Reload Agent
                        </Button>
                    </div>
                </div>
            );
        }

        return this.props.children;
    }
}

function AgentStudioPage() {
    const params = useParams();
    const router = useRouter();
    const agentId = params.id as string;

    // Available agents for orchestration child selector
    const { data: allCustomAgents = [] } = useCustomAgents();

    // Core state
    const [agent, setAgent] = useState<CustomAgent | null>(null);
    const [notFound, setNotFound] = useState(false);
    const [saving, setSaving] = useState(false);
    // Version history now loaded via TanStack Query in VersionsPanel
    const [publishDialogOpen, setPublishDialogOpen] = useState(false);

    // Agent Studio state
    const [testCaseRefreshTrigger, setTestCaseRefreshTrigger] = useState(0);
    const [versionRefreshTrigger, setVersionRefreshTrigger] = useState(0);
    const [isEvaluating, setIsEvaluating] = useState(false);
    const [evaluationProgress, setEvaluationProgress] = useState<{
        current: number;
        total: number;
    } | null>(null);

    // Context mode: Build (editor) vs Chat (test chat panel)
    const [contextMode, setContextMode] = useState<'build' | 'chat'>('build');

    // Auto-save state
    const [saveStatus, setSaveStatus] = useState<SaveStatus>('saved');
    const inflight = useRef<AbortController | null>(null);

    // Form state via custom hook (includes orchestration fields)
    const form = useAgentForm(agent);

    // Derive isGeneralAssistant from agent identity
    const isGeneralAssistant = agent?.name === 'General Assistant' || agent?.id === BUILTIN_AGENT_UUIDS['general'];

    // Navigation guard for unsaved changes (keyed on explicit save, not auto-save)
    useNavigationGuard({
        shouldBlock: form.hasChanges,
        message: 'You have unsaved draft changes. Are you sure you want to leave without saving?',
    });

    // Load agent data
    useEffect(() => {
        const loadAgent = () => {
            seedExampleAgents();

            const agentData = getCustomAgent(agentId);
            if (agentData) {
                setAgent(agentData);
                setNotFound(false);

                // Versions now loaded via TanStack Query in VersionsPanel
            } else {
                const newId = getMigratedAgentId(agentId);
                if (newId) {
                    router.replace(`/agents/${newId}`);
                    return;
                }
                setNotFound(true);
            }
        };

        loadAgent();
    }, [agentId, router]);

    // Auto-save: 2s trailing debounce → localStorage → async backend
    useEffect(() => {
        if (!form.hasChanges) {
            setSaveStatus('saved');
            return;
        }
        if (!agent) return;
        setSaveStatus('unsaved');

        const timer = setTimeout(() => {
            inflight.current?.abort();
            inflight.current = new AbortController();
            setSaveStatus('saving');

            // Sync save to localStorage
            updateCustomAgent(agentId, form.getUpdates());

            // Refresh agent in-memory (so hasChanges resets)
            const updated = getCustomAgent(agentId);
            if (updated) setAgent(updated);

            setSaveStatus('saved');
        }, AUTOSAVE_DEBOUNCE);

        return () => clearTimeout(timer);
    }, [form.hasChanges, form.name, form.description, form.systemPrompt, form.instructionFormat, form.model, form.temperature, form.maxTokens, form.selectedSkills, form.selectedConnectors, form.knowledgeBaseIds, form.workflowIds, form.deepWorkModels, form.isSupervisor, form.childAgentConfig, form.executionMode, form.resourceConstraints, agentId, agent]);

    const handleSave = async () => {
        if (!form.name.trim()) {
            toast.error('Validation Error', { description: 'Agent name is required' });
            return;
        }

        setSaving(true);

        try {
            const updated = updateCustomAgent(agentId, form.getUpdates());
            if (updated) {
                toast('Saved', { description: 'Agent draft saved' });
                setAgent(updated);
                setVersionRefreshTrigger((t) => t + 1);
                setSaveStatus('saved');
            } else {
                throw new Error('Failed to save');
            }
        } catch {
            toast.error('Error', { description: 'Failed to save agent' });
        } finally {
            setSaving(false);
        }
    };

    const handleTestCaseCreated = useCallback(() => {
        setTestCaseRefreshTrigger((t) => t + 1);
    }, []);

    const handleRunEvaluation = useCallback(
        async (versionId?: string) => {
            setIsEvaluating(true);
            setEvaluationProgress(null);

            runEvaluation(
                agentId,
                { version_id: versionId, baseline_only: false },
                {
                    onProgress: (progress) => {
                        setEvaluationProgress({ current: progress.completed, total: progress.total });
                    },
                    onResult: (result) => {
                        setIsEvaluating(false);
                        setEvaluationProgress(null);
                        setVersionRefreshTrigger((t) => t + 1);
                        setTestCaseRefreshTrigger((t) => t + 1);
                        toast('Evaluation Complete', { description: `Accuracy: ${(result.accuracy * 100).toFixed(1)}% (${result.passed_tests}/${result.total_tests} passed)` });
                    },
                    onError: (err) => {
                        setIsEvaluating(false);
                        setEvaluationProgress(null);
                        toast.error('Evaluation Failed', { description: err.message || 'Unknown error' });
                    },
                }
            );
        },
        [agentId]
    );

    // Keyboard shortcut for explicit save (Cmd+S)
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 's' && !e.shiftKey) {
                e.preventDefault();
                if (form.hasChanges && form.name.trim() && !saving) {
                    handleSave();
                }
            }
            // Cmd+Shift+C: Toggle Build/Chat context
            if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === 'c') {
                e.preventDefault();
                setContextMode((m) => (m === 'build' ? 'chat' : 'build'));
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [form.hasChanges, form.name, saving]);

    // Save status icon
    const SaveStatusIcon = () => {
        switch (saveStatus) {
            case 'saved':
                return <CloudCheck className="h-3.5 w-3.5 text-green-600" weight="fill" />;
            case 'saving':
                return <CloudArrowUp className="h-3.5 w-3.5 text-muted-foreground animate-pulse" />;
            case 'unsaved':
                return <CloudSlash className="h-3.5 w-3.5 text-amber-600" />;
        }
    };

    if (notFound) {
        return (
            <div className="p-6 max-w-2xl mx-auto">
                <Button
                    variant="ghost"
                    onClick={() => router.push('/agents')}
                    className="mb-4"
                >
                    <ArrowLeft className="mr-2 h-4 w-4" />
                    Back to Agents
                </Button>
                <Card>
                    <CardContent className="pt-6">
                        <div className="text-center space-y-4">
                            <Robot className="h-16 w-16 mx-auto text-muted-foreground/50" weight="duotone" />
                            <h2 className="text-xl font-semibold">Agent Not Found</h2>
                            <p className="text-muted-foreground">
                                The agent &quot;{agentId}&quot; doesn&apos;t exist or may have been deleted.
                            </p>
                            <Button onClick={() => router.push('/agents')}>Browse Agents</Button>
                        </div>
                    </CardContent>
                </Card>
            </div>
        );
    }

    if (!agent) {
        return (
            <div className="flex items-center justify-center h-64">
                <CircleNotch className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
        );
    }

    return (
        <div className="flex flex-col h-full overflow-hidden">
            {/* Header */}
            <header className="relative h-14 px-4 border-b bg-background flex items-center shrink-0">
                {/* Left: back + agent name + status */}
                <div className="flex items-center gap-3 min-w-0 flex-1">
                    <Button
                        variant="ghost"
                        size="icon"
                        className="shrink-0 h-8 w-8"
                        onClick={() => router.push('/agents')}
                    >
                        <ArrowLeft className="h-4 w-4" />
                    </Button>
                    <div className="w-8 h-8 rounded-lg bg-muted flex items-center justify-center shrink-0">
                        <Robot className="h-4 w-4 text-muted-foreground" weight="fill" />
                    </div>
                    <div className="min-w-0 flex items-center gap-2">
                        <span className="text-sm font-semibold truncate">{agent.name}</span>
                        <div className="flex items-center gap-1">
                            <SaveStatusIcon />
                            <span className="text-[10px] text-muted-foreground">
                                {saveStatus === 'saved' ? 'Saved' : saveStatus === 'saving' ? 'Saving...' : 'Unsaved'}
                            </span>
                        </div>
                        {agent.visibility && agent.visibility !== 'private' && (
                            <Badge variant="default" className="bg-emerald-600 text-[10px] py-0 h-5">
                                <Globe className="h-2.5 w-2.5 mr-0.5" weight="fill" />
                                Published
                            </Badge>
                        )}
                    </div>
                </div>

                {/* Center: Build / Chat context switcher (absolutely centered) */}
                <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                    <div className="flex items-center bg-muted rounded-lg p-0.5 pointer-events-auto">
                        <button
                            onClick={() => setContextMode('build')}
                            className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                                contextMode === 'build'
                                    ? 'bg-background text-foreground shadow-sm'
                                    : 'text-muted-foreground hover:text-foreground'
                            }`}
                        >
                            Build
                        </button>
                        <button
                            onClick={() => setContextMode('chat')}
                            className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                                contextMode === 'chat'
                                    ? 'bg-background text-foreground shadow-sm'
                                    : 'text-muted-foreground hover:text-foreground'
                            }`}
                        >
                            Chat
                        </button>
                    </div>
                </div>

                {/* Right: actions */}
                <div className="flex items-center gap-2">
                    {isEvaluating && evaluationProgress && (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground mr-2">
                            <CircleNotch className="h-3.5 w-3.5 animate-spin" />
                            <span>{evaluationProgress.current}/{evaluationProgress.total}</span>
                        </div>
                    )}
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleRunEvaluation()}
                        disabled={isEvaluating}
                        className="gap-1.5 h-8 text-xs"
                    >
                        <Play className="h-3.5 w-3.5" />
                        Evaluate
                    </Button>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={handleSave}
                        disabled={saving || !form.name.trim() || !form.hasChanges}
                        className="gap-1.5 h-8 text-xs"
                    >
                        {saving ? (
                            <CircleNotch className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                            <FloppyDisk className="h-3.5 w-3.5" />
                        )}
                        Save
                    </Button>
                    <Button
                        variant={agent?.visibility && agent.visibility !== 'private' ? 'outline' : 'default'}
                        size="sm"
                        onClick={() => setPublishDialogOpen(true)}
                        disabled={!agent || agent.id.startsWith('local-')}
                        className="gap-1.5 h-8 text-xs"
                    >
                        <Globe className="h-3.5 w-3.5" />
                        {agent?.visibility && agent.visibility !== 'private' ? 'Published' : 'Publish'}
                    </Button>
                </div>
            </header>

            {/* Content: full chat view OR editor + sidebar */}
            <div className="flex-1 min-h-0">
                {contextMode === 'chat' ? (
                    <AgentStudioChatPanel
                        agentId={agentId}
                        agentName={agent.name}
                        systemPrompt={form.systemPrompt}
                        model={form.model}
                        hasUnsavedConfig={form.hasChanges}
                        onTestCaseCreated={handleTestCaseCreated}
                    />
                ) : (
                    <V2Layout
                        editorPanel={
                            <div className="h-full flex flex-col">
                                <div className="px-6 pt-5 pb-1.5 shrink-0">
                                    <Input
                                        value={form.name}
                                        onChange={(e) => form.setName(e.target.value)}
                                        placeholder="Agent Name"
                                        className="border-none bg-transparent text-xl font-semibold p-0 h-auto leading-7 focus-visible:ring-0 placeholder:text-muted-foreground/40"
                                    />
                                    {form.description && (
                                        <p className="text-[13px] leading-5 text-muted-foreground mt-1">{form.description}</p>
                                    )}
                                </div>
                                <div className="flex-1 min-h-0">
                                    <InstructionsEditor
                                        content={form.systemPrompt}
                                        instructionFormat={form.instructionFormat}
                                        variant="document"
                                        onChange={(json, _plainText) => {
                                            if (form.instructionFormat === 'plain_text' && form.setInstructionFormat) {
                                                form.setInstructionFormat('tiptap_json');
                                            }
                                            form.setSystemPrompt(json);
                                        }}
                                    />
                                </div>
                            </div>
                        }
                        resourcesContent={
                            <ResourcesTab
                                selectedSkills={form.selectedSkills}
                                selectedConnectors={form.selectedConnectors}
                                knowledgeBaseIds={form.knowledgeBaseIds}
                                workflowIds={form.workflowIds}
                                toggleSkill={form.toggleSkill}
                                toggleConnector={form.toggleConnector}
                                toggleKnowledgeBase={form.toggleKnowledgeBase}
                                toggleWorkflow={form.toggleWorkflow}
                            />
                        }
                        gradersContent={
                            <TestCasesPanel
                                agentId={agentId}
                                instructions={form.systemPrompt}
                                refreshTrigger={testCaseRefreshTrigger}
                                onRunEvaluation={() => handleRunEvaluation()}
                            />
                        }
                        settingsContent={
                            <SettingsTab
                                name={form.name}
                                description={form.description}
                                setName={form.setName}
                                setDescription={form.setDescription}
                                model={form.model}
                                temperature={form.temperature}
                                maxTokens={form.maxTokens}
                                setModel={form.setModel}
                                setTemperature={form.setTemperature}
                                setMaxTokens={form.setMaxTokens}
                                deepWorkModels={form.deepWorkModels}
                                onDeepWorkModelsChange={form.setDeepWorkModels}
                                isSupervisor={form.isSupervisor}
                                executionMode={form.executionMode}
                                childAgentConfig={form.childAgentConfig}
                                resourceConstraints={form.resourceConstraints}
                                availableAgents={allCustomAgents
                                    .filter(a => a.id !== agentId)
                                    .map(a => ({ id: a.id, name: a.name }))}
                                onIsSupervisorChange={form.setIsSupervisor}
                                onExecutionModeChange={form.setExecutionMode}
                                onChildAgentConfigChange={form.setChildAgentConfig}
                                onResourceConstraintsChange={form.setResourceConstraints}
                                versionsContent={
                                    <VersionsPanel
                                        agentId={agentId}
                                        refreshTrigger={versionRefreshTrigger}
                                        onRunEvaluation={handleRunEvaluation}
                                    />
                                }
                            />
                        }
                        backgroundContent={
                            <BackgroundTab
                                backgroundConfig={agent?.backgroundConfig ?? null}
                                resourceConstraints={form.resourceConstraints}
                                cronExpression={agent?.backgroundConfig?.cron_expression ?? ''}
                                timezone={agent?.backgroundConfig?.timezone ?? 'UTC'}
                                onSave={async (config) => {
                                    try {
                                        updateCustomAgent(agentId, {
                                            backgroundConfig: {
                                                ...config.background_config,
                                                cron_expression: config.cron_expression,
                                                timezone: config.timezone,
                                            },
                                            resourceConstraints: config.resource_constraints,
                                        });
                                        toast.success('Background config saved');
                                    } catch {
                                        toast.error('Failed to save background config');
                                    }
                                }}
                            />
                        }
                    />
                )}
            </div>

            <PublishAgentDialog
                agent={agent}
                open={publishDialogOpen}
                onOpenChange={setPublishDialogOpen}
                onPublished={() => {
                    const updated = getCustomAgent(agentId);
                    if (updated) setAgent(updated);
                }}
            />
        </div>
    );
}

export default function AgentStudioPageWithErrorBoundary() {
    return (
        <AgentStudioErrorBoundary>
            <AgentStudioPage />
        </AgentStudioErrorBoundary>
    );
}
