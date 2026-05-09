'use client';

import { useCallback, useMemo, useState, useEffect } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Switch } from '@/components/ui/switch';
import { X, Play, Brain, Export, Database, Code, Globe, GitBranch, ArrowsClockwise, TextT, FunnelSimple, ChatTeardropText, Plus, Trash, CaretDown, CaretRight, Toolbox, Plugs, Robot, ChartLineUp, ShieldCheck, BookOpenText, MagnifyingGlass, CheckCircle, Circle, Sparkle, Warning, Timer, Equals, ListBullets, Note, FileText, Crosshair, Scan, Bell, Link, TreeStructure, UserCircle } from '@phosphor-icons/react';
import { useWorkflowBuilderLayout } from '@/lib/stores/workflow-builder-layout';
import type { Node } from '@xyflow/react';
import { PromptEditor } from './PromptEditor';
import { ConditionBuilder, type ConditionGroup, type Condition } from './ConditionBuilder';
import { ModelSelector } from './ModelSelector';
import { SkillSelector } from '@/components/skills/SkillSelector';
import { skillsApi, type Skill } from '@/lib/api/skills';
import { getCustomAgents, type CustomAgent } from '@/lib/api/agents';
import { WorkflowSelector } from './WorkflowSelector';
import { useKnowledgeBases } from '@/hooks/useKnowledgeBases';
import { Loader2 } from 'lucide-react';
import { useConnectors, useEnabledConnectors, useConnectorDetail, useEnableConnector } from '@/hooks/useMarketplace';
import { EnableConnectorDialog } from '@/components/marketplace/EnableConnectorDialog';
import type { MarketplaceConnector } from '@/lib/api/marketplace';
import { toast } from 'sonner';

interface ConfigPanelProps {
    node: Node | null;
    nodes: Node[];
    workflowId?: string;
    onClose: () => void;
    onUpdate: (nodeId: string, data: Record<string, unknown>) => void;
}

const nodeIcons: Record<string, React.ElementType> = {
    start: Play,
    llm: Brain,
    output: Export,
    knowledge: Database,
    code: Code,
    http: Globe,
    ifelse: GitBranch,
    iteration: ArrowsClockwise,
    template: TextT,
    aggregator: FunnelSimple,
    classifier: ChatTeardropText,
    skill: Toolbox,
    connector: Plugs,
    agent: Robot,
    data: Database,
    analysis: ChartLineUp,
    compliance: ShieldCheck,
    research: BookOpenText,
    // Phase 3: Essential workflow nodes
    wait: Timer,
    variable_assigner: Equals,
    list_operator: ListBullets,
    notes: Note,
    document_extractor: FileText,
    parameter_extractor: Crosshair,
    ocr: Scan,
    notification: Bell,
    webhook_trigger: Link,
    subworkflow: TreeStructure,
    human_in_loop: UserCircle,
};

const nodeColors: Record<string, string> = {
    start: 'bg-gray-100',
    llm: 'bg-purple-100',
    output: 'bg-teal-100',
    knowledge: 'bg-green-100',
    code: 'bg-blue-100',
    http: 'bg-orange-100',
    ifelse: 'bg-yellow-100',
    iteration: 'bg-indigo-100',
    template: 'bg-pink-100',
    aggregator: 'bg-cyan-100',
    classifier: 'bg-violet-100',
    skill: 'bg-emerald-100',
    connector: 'bg-teal-100',
    agent: 'bg-indigo-100',
    data: 'bg-blue-100',
    analysis: 'bg-green-100',
    compliance: 'bg-purple-100',
    research: 'bg-orange-100',
    // Phase 3: Essential workflow nodes
    wait: 'bg-indigo-100',
    variable_assigner: 'bg-cyan-100',
    list_operator: 'bg-cyan-100',
    notes: 'bg-yellow-100',
    document_extractor: 'bg-green-100',
    parameter_extractor: 'bg-violet-100',
    ocr: 'bg-sky-100',
    notification: 'bg-orange-100',
    webhook_trigger: 'bg-orange-100',
    subworkflow: 'bg-indigo-100',
    human_in_loop: 'bg-red-100',
};

// Knowledge bases are now loaded from API via useKnowledgeBases hook

// Skill category colors for visual consistency
const SKILL_CATEGORY_COLORS: Record<string, { bg: string; text: string; border: string }> = {
    web: { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-200' },
    code: { bg: 'bg-green-50', text: 'text-green-700', border: 'border-green-200' },
    file: { bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-200' },
    memory: { bg: 'bg-purple-50', text: 'text-purple-700', border: 'border-purple-200' },
    mcp: { bg: 'bg-indigo-50', text: 'text-indigo-700', border: 'border-indigo-200' },
};

// Connector type colors
const CONNECTOR_TYPE_COLORS: Record<string, { bg: string; text: string; border: string }> = {
    mcp: { bg: 'bg-indigo-50', text: 'text-indigo-700', border: 'border-indigo-200' },
    rest: { bg: 'bg-teal-50', text: 'text-teal-700', border: 'border-teal-200' },
    graphql: { bg: 'bg-pink-50', text: 'text-pink-700', border: 'border-pink-200' },
    itsm: { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-200' },
    communication: { bg: 'bg-green-50', text: 'text-green-700', border: 'border-green-200' },
    crm: { bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-200' },
    custom: { bg: 'bg-gray-50', text: 'text-gray-700', border: 'border-gray-200' },
};

// Analysis types for the Analysis node
const ANALYSIS_TYPES = [
    { id: 'fundamental', name: 'Fundamental Analysis', description: 'P/E, P/B, ROE, financial ratios' },
    { id: 'technical', name: 'Technical Analysis', description: 'Charts, patterns, indicators' },
    { id: 'sentiment', name: 'Sentiment Analysis', description: 'News, social media, market mood' },
    { id: 'quantitative', name: 'Quantitative Analysis', description: 'Statistical models, backtesting' },
];

// Compliance check types
const COMPLIANCE_CHECK_TYPES = [
    { id: 'sox', name: 'SOX Compliance', description: 'Sarbanes-Oxley Act checks' },
    { id: 'mifid', name: 'MiFID II', description: 'Markets in Financial Instruments' },
    { id: 'basel', name: 'Basel III', description: 'Capital requirements' },
    { id: 'aml', name: 'AML/KYC', description: 'Anti-Money Laundering checks' },
    { id: 'gdpr', name: 'GDPR', description: 'Data protection compliance' },
    { id: 'custom', name: 'Custom Rules', description: 'Organization-specific rules' },
];

export function ConfigPanel({ node, nodes, workflowId, onClose, onUpdate }: ConfigPanelProps) {
    const { toggleConfigPanel } = useWorkflowBuilderLayout();
    const [showKnowledgeSelector, setShowKnowledgeSelector] = useState(false);
    const [availableSkills, setAvailableSkills] = useState<Skill[]>([]);
    const [availableAgents, setAvailableAgents] = useState<CustomAgent[]>([]);
    const [skillSearch, setSkillSearch] = useState('');
    const [connectorSearch, setConnectorSearch] = useState('');
    const [agentSearch, setAgentSearch] = useState('');
    const [showSkillDropdown, setShowSkillDropdown] = useState(false);
    const [showConnectorDropdown, setShowConnectorDropdown] = useState(false);
    const [showAgentDropdown, setShowAgentDropdown] = useState(false);

    // Connector enable dialog state
    const [connectorToEnable, setConnectorToEnable] = useState<string | null>(null);

    // Load knowledge bases from API
    const { data: knowledgeBases = [], isLoading: kbLoading } = useKnowledgeBases();

    // Load marketplace connectors with enabled status
    const { data: marketplaceConnectors = [], isLoading: connectorsLoading } = useConnectors();
    const { data: enabledData } = useEnabledConnectors();
    const { data: connectorDetail, isLoading: detailLoading } = useConnectorDetail(connectorToEnable ?? undefined);
    const enableMutation = useEnableConnector();

    // Create a set of enabled connector IDs for quick lookup
    const enabledConnectorIds = useMemo(() => {
        return new Set(enabledData?.connectors?.map(c => c.connector_id) || []);
    }, [enabledData]);

    // Merge marketplace connectors with enabled status
    const availableConnectors = useMemo(() => {
        return marketplaceConnectors.map(c => ({
            ...c,
            is_enabled: enabledConnectorIds.has(c.id) || c.is_enabled,
        }));
    }, [marketplaceConnectors, enabledConnectorIds]);

    const nodeData = useMemo(() => (node?.data as Record<string, unknown>) || {}, [node?.data]);
    const nodeId = node?.id || '';
    const nodeType = node?.type || '';

    // Load available skills and agents
    useEffect(() => {
        const loadData = async () => {
            try {
                const skills = await skillsApi.listSkills();
                setAvailableSkills(skills);
            } catch (error) {
                console.warn('Failed to load skills:', error);
            }
            // Load agents from localStorage
            setAvailableAgents(getCustomAgents());
        };
        loadData();
    }, []);

    // Handle enabling a connector from the dialog
    const handleEnableConnector = async (credentials: Record<string, string>) => {
        if (!connectorToEnable) return;

        try {
            const result = await enableMutation.mutateAsync({
                connectorId: connectorToEnable,
                request: { credentials },
            });
            toast.success(result.message);
            setConnectorToEnable(null);
        } catch (error: any) {
            toast.error(error.message || 'Failed to enable connector');
        }
    };

    const handleChange = useCallback(
        (key: string, value: unknown) => {
            if (nodeId) {
                onUpdate(nodeId, { ...nodeData, [key]: value });
            }
        },
        [nodeId, nodeData, onUpdate]
    );

    if (!node) return null;

    const Icon = nodeIcons[nodeType] || Play;
    const bgColor = nodeColors[nodeType] || 'bg-gray-100';

    // ============ START NODE ============
    const renderStartConfig = () => (
        <div className="space-y-4">
            <div className="space-y-2">
                <Label>Node Label</Label>
                <Input
                    value={(nodeData.label as string) || ''}
                    onChange={(e) => handleChange('label', e.target.value)}
                    placeholder="Start"
                />
            </div>

            <div className="space-y-2">
                <Label>Input Variables</Label>
                <div className="space-y-2">
                    {((nodeData.variables as Array<{ name: string; type: string }>) || []).map((v, i) => (
                        <div key={i} className="flex items-center gap-2">
                            <Input
                                className="flex-1"
                                value={v.name}
                                placeholder="Variable name"
                                onChange={(e) => {
                                    const vars = [...((nodeData.variables as Array<{ name: string; type: string }>) || [])];
                                    vars[i] = { ...vars[i], name: e.target.value };
                                    handleChange('variables', vars);
                                }}
                            />
                            <select
                                className="h-9 px-2 rounded-md border bg-background text-sm"
                                value={v.type}
                                onChange={(e) => {
                                    const vars = [...((nodeData.variables as Array<{ name: string; type: string }>) || [])];
                                    vars[i] = { ...vars[i], type: e.target.value };
                                    handleChange('variables', vars);
                                }}
                            >
                                <option value="string">String</option>
                                <option value="number">Number</option>
                                <option value="array">Array</option>
                                <option value="object">Object</option>
                            </select>
                            <Button
                                size="icon"
                                variant="ghost"
                                className="h-9 w-9"
                                onClick={() => {
                                    const vars = ((nodeData.variables as Array<{ name: string; type: string }>) || []).filter((_, idx) => idx !== i);
                                    handleChange('variables', vars);
                                }}
                            >
                                <Trash className="h-4 w-4 text-red-500" />
                            </Button>
                        </div>
                    ))}
                    <Button
                        size="sm"
                        variant="outline"
                        className="w-full"
                        onClick={() => {
                            const vars = [...((nodeData.variables as Array<{ name: string; type: string }>) || []), { name: '', type: 'string' }];
                            handleChange('variables', vars);
                        }}
                    >
                        <Plus className="h-3 w-3 mr-1" />
                        Add Variable
                    </Button>
                </div>
            </div>
        </div>
    );

    // ============ LLM NODE ============
    const renderLLMConfig = () => (
        <div className="space-y-4">
            <ModelSelector
                value={(nodeData.model as string) || ''}
                onChange={(v) => handleChange('model', v)}
            />

            <PromptEditor
                label="System Prompt"
                value={(nodeData.systemPrompt as string) || ''}
                onChange={(v) => handleChange('systemPrompt', v)}
                placeholder="You are a helpful assistant..."
                nodes={nodes}
                currentNodeId={nodeId}
                minHeight="80px"
            />

            <PromptEditor
                label="User Prompt"
                value={(nodeData.userPrompt as string) || ''}
                onChange={(v) => handleChange('userPrompt', v)}
                placeholder="Use {{variable}} to reference inputs"
                nodes={nodes}
                currentNodeId={nodeId}
                minHeight="80px"
            />

            <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                    <Label>Temperature</Label>
                    <Input
                        type="number"
                        min="0"
                        max="2"
                        step="0.1"
                        value={(nodeData.temperature as number) || 0.7}
                        onChange={(e) => handleChange('temperature', parseFloat(e.target.value))}
                    />
                </div>
                <div className="space-y-2">
                    <Label>Max Tokens</Label>
                    <Input
                        type="number"
                        min="1"
                        max="128000"
                        value={(nodeData.maxTokens as number) || 4096}
                        onChange={(e) => handleChange('maxTokens', parseInt(e.target.value))}
                    />
                </div>
            </div>

            {renderSkillsConfig()}
        </div>
    );

    // ============ OUTPUT NODE ============
    const renderOutputConfig = () => (
        <div className="space-y-4">
            <div className="space-y-2">
                <Label>Output Variable</Label>
                <Input
                    value={(nodeData.outputName as string) || 'output'}
                    onChange={(e) => handleChange('outputName', e.target.value)}
                    placeholder="output"
                />
            </div>

            <PromptEditor
                label="Output Value"
                value={(nodeData.outputValue as string) || ''}
                onChange={(v) => handleChange('outputValue', v)}
                placeholder="Select variable to output..."
                nodes={nodes}
                currentNodeId={nodeId}
                minHeight="60px"
            />

            <div className="flex items-center justify-between">
                <Label>Stream Output</Label>
                <Switch
                    checked={(nodeData.streamOutput as boolean) || false}
                    onCheckedChange={(checked) => handleChange('streamOutput', checked)}
                />
            </div>
        </div>
    );

    // ============ KNOWLEDGE NODE ============
    const renderKnowledgeConfig = () => {
        const selectedKBs = (nodeData.knowledgeBases as string[]) || [];

        return (
            <div className="space-y-4">
                <PromptEditor
                    label="Query"
                    value={(nodeData.query as string) || ''}
                    onChange={(v) => handleChange('query', v)}
                    placeholder="Enter search query or select variable..."
                    nodes={nodes}
                    currentNodeId={nodeId}
                    minHeight="60px"
                />

                <div className="space-y-2">
                    <Label>Knowledge Bases</Label>
                    <div className="space-y-2">
                        {selectedKBs.map((kbId) => {
                            const kb = knowledgeBases.find((k) => k.id === kbId);
                            return (
                                <div key={kbId} className="flex items-center justify-between p-2 border rounded bg-muted/50">
                                    <span className="text-sm">{kb?.name || kbId}</span>
                                    <Button
                                        size="icon"
                                        variant="ghost"
                                        className="h-6 w-6"
                                        onClick={() => handleChange('knowledgeBases', selectedKBs.filter((id) => id !== kbId))}
                                    >
                                        <X className="h-3 w-3" />
                                    </Button>
                                </div>
                            );
                        })}
                        <div className="relative">
                            <Button
                                size="sm"
                                variant="outline"
                                className="w-full"
                                onClick={() => setShowKnowledgeSelector(!showKnowledgeSelector)}
                                disabled={kbLoading}
                            >
                                {kbLoading ? (
                                    <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                                ) : (
                                    <Plus className="h-3 w-3 mr-1" />
                                )}
                                Add Knowledge Base
                                <CaretDown className="h-3 w-3 ml-auto" />
                            </Button>
                            {showKnowledgeSelector && (
                                <div className="absolute z-10 top-full left-0 right-0 mt-1 bg-background border rounded-md shadow-lg p-2">
                                    {knowledgeBases.length === 0 ? (
                                        <div className="text-muted-foreground text-sm px-2 py-1.5">
                                            No knowledge bases found. Upload documents to create one.
                                        </div>
                                    ) : knowledgeBases.filter((kb) => !selectedKBs.includes(kb.id)).length === 0 ? (
                                        <div className="text-muted-foreground text-sm px-2 py-1.5">
                                            All knowledge bases already selected.
                                        </div>
                                    ) : (
                                        knowledgeBases.filter((kb) => !selectedKBs.includes(kb.id)).map((kb) => (
                                            <button
                                                key={kb.id}
                                                className="w-full text-left px-2 py-1.5 text-sm hover:bg-muted rounded"
                                                onClick={() => {
                                                    handleChange('knowledgeBases', [...selectedKBs, kb.id]);
                                                    setShowKnowledgeSelector(false);
                                                }}
                                            >
                                                {kb.name}
                                            </button>
                                        ))
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                </div>

                <div className="space-y-3 pt-2 border-t">
                    <Label>Retrieval Settings</Label>
                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <Label className="text-xs text-muted-foreground">Top K</Label>
                            <Input
                                type="number"
                                min="1"
                                max="20"
                                value={(nodeData.topK as number) || 5}
                                onChange={(e) => handleChange('topK', parseInt(e.target.value))}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label className="text-xs text-muted-foreground">Score Threshold</Label>
                            <Input
                                type="number"
                                min="0"
                                max="1"
                                step="0.1"
                                value={(nodeData.scoreThreshold as number) || 0.5}
                                onChange={(e) => handleChange('scoreThreshold', parseFloat(e.target.value))}
                            />
                        </div>
                    </div>

                    <div className="flex items-center justify-between">
                        <Label className="text-xs text-muted-foreground">Enable Reranking</Label>
                        <Switch
                            checked={(nodeData.enableReranking as boolean) || false}
                            onCheckedChange={(checked) => handleChange('enableReranking', checked)}
                        />
                    </div>
                </div>
            </div>
        );
    };

    // ============ CODE NODE ============
    const renderCodeConfig = () => (
        <div className="space-y-4">
            <div className="space-y-2">
                <Label>Language</Label>
                <select
                    className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                    value={(nodeData.language as string) || 'python3'}
                    onChange={(e) => handleChange('language', e.target.value)}
                >
                    <option value="python3">Python 3</option>
                    <option value="javascript">JavaScript</option>
                </select>
            </div>
            <div className="space-y-2">
                <Label>Code</Label>
                <textarea
                    className="w-full min-h-[150px] rounded-md border px-3 py-2 text-sm bg-gray-900 text-gray-100 font-mono resize-none"
                    value={(nodeData.code as string) || ''}
                    onChange={(e) => handleChange('code', e.target.value)}
                    placeholder="def main(arg1, arg2):\n    return {'result': arg1 + arg2}"
                />
            </div>
            <div className="space-y-2">
                <Label>Input Variables</Label>
                <div className="space-y-1">
                    {((nodeData.inputs as string[]) || []).map((input, i) => (
                        <div key={i} className="flex items-center gap-2">
                            <Input
                                className="flex-1"
                                value={input}
                                onChange={(e) => {
                                    const inputs = [...((nodeData.inputs as string[]) || [])];
                                    inputs[i] = e.target.value;
                                    handleChange('inputs', inputs);
                                }}
                            />
                            <Button
                                size="icon"
                                variant="ghost"
                                className="h-8 w-8"
                                onClick={() => {
                                    const inputs = ((nodeData.inputs as string[]) || []).filter((_, idx) => idx !== i);
                                    handleChange('inputs', inputs);
                                }}
                            >
                                <Trash className="h-3 w-3 text-red-500" />
                            </Button>
                        </div>
                    ))}
                    <Button
                        size="sm"
                        variant="ghost"
                        className="w-full text-xs"
                        onClick={() => handleChange('inputs', [...((nodeData.inputs as string[]) || []), ''])}
                    >
                        <Plus className="h-3 w-3 mr-1" />
                        Add input
                    </Button>
                </div>
            </div>

            {renderSkillsConfig()}
        </div>
    );

    // ============ HTTP NODE ============
    const renderHttpConfig = () => (
        <div className="space-y-4">
            <div className="grid grid-cols-3 gap-2">
                <div className="space-y-2">
                    <Label>Method</Label>
                    <select
                        className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                        value={(nodeData.method as string) || 'GET'}
                        onChange={(e) => handleChange('method', e.target.value)}
                    >
                        <option value="GET">GET</option>
                        <option value="POST">POST</option>
                        <option value="PUT">PUT</option>
                        <option value="DELETE">DELETE</option>
                        <option value="PATCH">PATCH</option>
                    </select>
                </div>
                <div className="col-span-2 space-y-2">
                    <Label>URL</Label>
                    <Input
                        value={(nodeData.url as string) || ''}
                        onChange={(e) => handleChange('url', e.target.value)}
                        placeholder="https://api.example.com/endpoint"
                    />
                </div>
            </div>
            <div className="space-y-2">
                <Label>Headers (JSON)</Label>
                <textarea
                    className="w-full min-h-[60px] rounded-md border px-3 py-2 text-sm font-mono bg-background resize-none"
                    value={JSON.stringify(nodeData.headers || {}, null, 2)}
                    onChange={(e) => {
                        try {
                            handleChange('headers', JSON.parse(e.target.value));
                        } catch {
                            // Invalid JSON, ignore
                        }
                    }}
                    placeholder='{"Authorization": "Bearer ..."}'
                />
            </div>
            <div className="space-y-2">
                <Label>Body</Label>
                <textarea
                    className="w-full min-h-[80px] rounded-md border px-3 py-2 text-sm font-mono bg-background resize-none"
                    value={(nodeData.body as string) || ''}
                    onChange={(e) => handleChange('body', e.target.value)}
                    placeholder="Request body (JSON or text)"
                />
            </div>
            <div className="space-y-2">
                <Label>Timeout (seconds)</Label>
                <Input
                    type="number"
                    min="1"
                    max="300"
                    value={(nodeData.timeout as number) || 30}
                    onChange={(e) => handleChange('timeout', parseInt(e.target.value))}
                />
            </div>
        </div>
    );

    // ============ IF/ELSE NODE ============
    const renderIfElseConfig = () => {
        // Migrate old flat conditions format to new ConditionGroup format
        const rawConditions = nodeData.conditions as unknown;
        let conditions: ConditionGroup[] = [];

        if (Array.isArray(rawConditions) && rawConditions.length > 0) {
            // Check if it's the old format (flat array of conditions)
            const firstItem = rawConditions[0];
            if (firstItem && 'variable' in firstItem && !('conditions' in firstItem)) {
                // Old format: convert to new ConditionGroup format
                conditions = [{
                    id: `group_${Date.now()}`,
                    logic: (nodeData.logicalOperator as 'and' | 'or') || 'and',
                    conditions: rawConditions as Condition[],
                }];
            } else {
                // New format: use as-is
                conditions = rawConditions as ConditionGroup[];
            }
        }

        return (
            <div className="space-y-4">
                <div className="space-y-2">
                    <Label>Conditions</Label>
                    <ConditionBuilder
                        conditions={conditions}
                        onChange={(conditions) => handleChange('conditions', conditions)}
                        nodes={nodes}
                        currentNodeId={nodeId}
                    />
                </div>

                <div className="text-xs text-muted-foreground bg-muted/50 p-3 rounded-lg">
                    <p><strong>IF</strong> branch executes when conditions are true</p>
                    <p><strong>ELSE</strong> branch executes otherwise</p>
                </div>
            </div>
        );
    };

    // ============ ITERATION NODE ============
    const renderIterationConfig = () => (
        <div className="space-y-4">
            <PromptEditor
                label="Input Array"
                value={(nodeData.inputArray as string) || ''}
                onChange={(v) => handleChange('inputArray', v)}
                placeholder="Select array variable to iterate..."
                nodes={nodes}
                currentNodeId={nodeId}
                minHeight="60px"
            />

            <div className="flex items-center justify-between">
                <div>
                    <Label>Parallel Mode</Label>
                    <p className="text-xs text-muted-foreground">Run iterations concurrently</p>
                </div>
                <Switch
                    checked={(nodeData.parallel as boolean) || false}
                    onCheckedChange={(checked) => handleChange('parallel', checked)}
                />
            </div>

            <div className="space-y-2">
                <Label>Error Handling</Label>
                <select
                    className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                    value={(nodeData.errorHandling as string) || 'terminate'}
                    onChange={(e) => handleChange('errorHandling', e.target.value)}
                >
                    <option value="terminate">Terminate on error</option>
                    <option value="ignore">Ignore and continue</option>
                </select>
            </div>

            <div className="flex items-center justify-between">
                <div>
                    <Label>Flatten Output</Label>
                    <p className="text-xs text-muted-foreground">Flatten nested arrays</p>
                </div>
                <Switch
                    checked={(nodeData.flattenOutput as boolean) || false}
                    onCheckedChange={(checked) => handleChange('flattenOutput', checked)}
                />
            </div>
        </div>
    );

    // ============ TEMPLATE NODE ============
    const renderTemplateConfig = () => (
        <div className="space-y-4">
            <PromptEditor
                label="Template"
                value={(nodeData.template as string) || ''}
                onChange={(v) => handleChange('template', v)}
                placeholder="Hello {{name}}, your order {{order_id}} is ready."
                nodes={nodes}
                currentNodeId={nodeId}
                minHeight="120px"
            />
        </div>
    );

    // ============ AGGREGATOR NODE ============
    const renderAggregatorConfig = () => {
        const selectedVars = (nodeData.variables as string[]) || [];

        return (
            <div className="space-y-4">
                <div className="space-y-2">
                    <Label>Variables to Aggregate</Label>
                    <div className="space-y-2">
                        {selectedVars.map((varPath, i) => (
                            <div key={i} className="flex items-center justify-between p-2 border rounded bg-muted/50">
                                <code className="text-xs">{varPath}</code>
                                <Button
                                    size="icon"
                                    variant="ghost"
                                    className="h-6 w-6"
                                    onClick={() => handleChange('variables', selectedVars.filter((_, idx) => idx !== i))}
                                >
                                    <X className="h-3 w-3" />
                                </Button>
                            </div>
                        ))}
                        <Button
                            size="sm"
                            variant="outline"
                            className="w-full"
                            onClick={() => handleChange('variables', [...selectedVars, ''])}
                        >
                            <Plus className="h-3 w-3 mr-1" />
                            Add Variable
                        </Button>
                    </div>
                </div>

                <div className="flex items-center justify-between">
                    <div>
                        <Label>Group Variables</Label>
                        <p className="text-xs text-muted-foreground">Combine into single object</p>
                    </div>
                    <Switch
                        checked={(nodeData.group as boolean) || true}
                        onCheckedChange={(checked) => handleChange('group', checked)}
                    />
                </div>

                <div className="space-y-2">
                    <Label>Output Name</Label>
                    <Input
                        value={(nodeData.outputName as string) || 'aggregated'}
                        onChange={(e) => handleChange('outputName', e.target.value)}
                        placeholder="aggregated"
                    />
                </div>
            </div>
        );
    };

    // ============ CLASSIFIER NODE ============
    const renderClassifierConfig = () => {
        // Support both 'classes' (new) and 'categories' (legacy) property names
        const classes = (nodeData.classes as Array<{ id: string; name: string }>) ||
                        (nodeData.categories as Array<{ id: string; name: string }>) || [];

        return (
            <div className="space-y-4">
                <PromptEditor
                    label="Input Text"
                    value={(nodeData.inputText as string) || ''}
                    onChange={(v) => handleChange('inputText', v)}
                    placeholder="Enter text or select variable to classify..."
                    nodes={nodes}
                    currentNodeId={nodeId}
                    minHeight="60px"
                />

                <ModelSelector
                    value={(nodeData.model as string) || ''}
                    onChange={(v) => handleChange('model', v)}
                />

                <div className="space-y-2">
                    <Label>Classes</Label>
                    <div className="space-y-2">
                        {classes.map((cls, i) => (
                            <div key={cls.id || `cls-${i}`} className="flex items-center gap-2">
                                <span className="w-6 h-6 rounded-full bg-primary/10 text-primary flex items-center justify-center text-xs">
                                    {i + 1}
                                </span>
                                <Input
                                    className="flex-1"
                                    value={cls.name}
                                    placeholder="Class name"
                                    onChange={(e) => {
                                        const newClasses = [...classes];
                                        newClasses[i] = { ...newClasses[i], name: e.target.value };
                                        handleChange('classes', newClasses);
                                    }}
                                />
                                <Button
                                    size="icon"
                                    variant="ghost"
                                    className="h-8 w-8"
                                    onClick={() => handleChange('classes', classes.filter((_, idx) => idx !== i))}
                                >
                                    <Trash className="h-3 w-3 text-red-500" />
                                </Button>
                            </div>
                        ))}
                        <Button
                            size="sm"
                            variant="outline"
                            className="w-full"
                            onClick={() => handleChange('classes', [...classes, { id: `class_${Date.now()}`, name: '' }])}
                        >
                            <Plus className="h-3 w-3 mr-1" />
                            Add Class
                        </Button>
                    </div>
                </div>

                <div className="space-y-2">
                    <Label>Instructions</Label>
                    <textarea
                        className="w-full min-h-[80px] rounded-md border px-3 py-2 text-sm bg-background resize-none"
                        value={(nodeData.instructions as string) || ''}
                        onChange={(e) => handleChange('instructions', e.target.value)}
                        placeholder="Provide specific guidelines for classification..."
                    />
                </div>

                <div className="flex items-center justify-between">
                    <div>
                        <Label>Include Memory</Label>
                        <p className="text-xs text-muted-foreground">Use conversation history</p>
                    </div>
                    <Switch
                        checked={(nodeData.includeMemory as boolean) || false}
                        onCheckedChange={(checked) => handleChange('includeMemory', checked)}
                    />
                </div>
            </div>
        );
    };

    const renderSkillsConfig = () => (
        <div className="space-y-4 pt-4 border-t mt-4">
            <Label className="text-base font-semibold">Agent Skills</Label>
            <SkillSelector
                value={(nodeData.skills as string[]) || []}
                onChange={(skills) => handleChange('skills', skills)}
                inheritDefaults={(nodeData.inheritSkills as boolean) ?? true}
                onInheritChange={(checked) => handleChange('inheritSkills', checked)}
            />
        </div>
    );

    // ============ SKILL NODE ============
    const renderSkillNodeConfig = () => {
        const selectedSkillId = nodeData.skillName as string;
        const selectedSkill = availableSkills.find(s => s.name === selectedSkillId || s.id === selectedSkillId);
        // If skill isn't found in availableSkills but we have skill data saved, create a placeholder skill object
        const displaySkill = selectedSkill || (selectedSkillId && nodeData.skillCategory ? {
            id: selectedSkillId,
            name: selectedSkillId,
            category: nodeData.skillCategory as string,
            description: (nodeData.skillDescription as string) || '',
        } : null);
        const filteredSkills = availableSkills.filter(s =>
            s.name.toLowerCase().includes(skillSearch.toLowerCase()) ||
            s.category.toLowerCase().includes(skillSearch.toLowerCase())
        );
        const inputs = (nodeData.inputs as Record<string, string>) || {};
        const categoryColors = displaySkill ? (SKILL_CATEGORY_COLORS[displaySkill.category] || SKILL_CATEGORY_COLORS.mcp) : null;

        return (
            <div className="space-y-4">
                {/* Node Label */}
                <div className="space-y-2">
                    <Label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Node Label</Label>
                    <Input
                        value={(nodeData.label as string) || ''}
                        onChange={(e) => handleChange('label', e.target.value)}
                        placeholder="Skill"
                        className="h-9"
                    />
                </div>

                {/* Skill Selection - Enhanced Dropdown */}
                <div className="space-y-2">
                    <Label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Select Skill</Label>
                    <div className="relative">
                        <button
                            type="button"
                            className={`w-full flex items-center gap-3 p-3 border-2 rounded-xl cursor-pointer transition-all duration-200 text-left ${
                                displaySkill
                                    ? `${categoryColors?.bg} ${categoryColors?.border} hover:shadow-md`
                                    : 'bg-muted/20 border-dashed border-muted-foreground/30 hover:border-muted-foreground/50 hover:bg-muted/30'
                            }`}
                            onClick={() => setShowSkillDropdown(!showSkillDropdown)}
                        >
                            {displaySkill ? (
                                <>
                                    <div className={`w-10 h-10 rounded-lg ${categoryColors?.bg} border ${categoryColors?.border} flex items-center justify-center`}>
                                        <Toolbox className={`h-5 w-5 ${categoryColors?.text}`} weight="fill" />
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <div className={`font-semibold text-sm ${categoryColors?.text}`}>{displaySkill.name}</div>
                                        <div className="text-xs text-muted-foreground capitalize">{displaySkill.category}</div>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <CheckCircle className={`h-5 w-5 ${categoryColors?.text}`} weight="fill" />
                                        <CaretDown className={`h-4 w-4 ${categoryColors?.text} transition-transform ${showSkillDropdown ? 'rotate-180' : ''}`} />
                                    </div>
                                </>
                            ) : (
                                <>
                                    <div className="w-10 h-10 rounded-lg bg-muted/50 border border-dashed border-muted-foreground/30 flex items-center justify-center">
                                        <Toolbox className="h-5 w-5 text-muted-foreground/50" />
                                    </div>
                                    <div className="flex-1">
                                        <div className="text-sm text-muted-foreground">Select a skill...</div>
                                        <div className="text-xs text-muted-foreground/70">Choose from available skills</div>
                                    </div>
                                    <CaretDown className={`h-4 w-4 text-muted-foreground transition-transform ${showSkillDropdown ? 'rotate-180' : ''}`} />
                                </>
                            )}
                        </button>

                        {showSkillDropdown && (
                            <div className="absolute z-50 top-full left-0 right-0 mt-2 bg-background border-2 rounded-xl shadow-xl max-h-[400px] overflow-hidden">
                                <div className="p-3 border-b bg-muted/30">
                                    <div className="relative">
                                        <MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                                        <Input
                                            value={skillSearch}
                                            onChange={(e) => setSkillSearch(e.target.value)}
                                            placeholder="Search skills..."
                                            className="pl-9 h-9 bg-background"
                                            autoFocus
                                        />
                                    </div>
                                    <div className="text-xs text-muted-foreground mt-2 flex items-center justify-between">
                                        <span>{filteredSkills.length} of {availableSkills.length} skills</span>
                                        {displaySkill && (
                                            <button
                                                className="text-red-500 hover:text-red-600 text-xs"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    // Clear all skill properties in a single call
                                                    if (nodeId) {
                                                        onUpdate(nodeId, {
                                                            ...nodeData,
                                                            skillName: '',
                                                            skillCategory: '',
                                                            skillDescription: '',
                                                        });
                                                    }
                                                }}
                                            >
                                                Clear selection
                                            </button>
                                        )}
                                    </div>
                                </div>
                                <ScrollArea className="h-[300px]">
                                    <div className="p-2">
                                        {/* Group skills by category */}
                                        {Object.entries(
                                            filteredSkills.reduce((acc, skill) => {
                                                const cat = skill.category || 'other';
                                                if (!acc[cat]) acc[cat] = [];
                                                acc[cat].push(skill);
                                                return acc;
                                            }, {} as Record<string, typeof filteredSkills>)
                                        ).sort(([a], [b]) => a.localeCompare(b)).map(([category, skills]) => {
                                            const colors = SKILL_CATEGORY_COLORS[category] || SKILL_CATEGORY_COLORS.mcp;
                                            return (
                                                <div key={category} className="mb-3">
                                                    <div className={`sticky top-0 px-3 py-1.5 text-xs font-bold ${colors.bg} ${colors.text} rounded-lg mb-2 uppercase tracking-wide`}>
                                                        {category} ({skills.length})
                                                    </div>
                                                    <div className="space-y-1">
                                                        {skills.map((skill) => {
                                                            const isSelected = selectedSkillId === skill.name || selectedSkillId === skill.id;
                                                            return (
                                                                <button
                                                                    key={skill.id}
                                                                    type="button"
                                                                    className={`w-full text-left p-3 rounded-lg flex items-start gap-3 transition-all duration-150 ${
                                                                        isSelected
                                                                            ? `${colors.bg} border-2 ${colors.border} shadow-sm`
                                                                            : 'hover:bg-muted/50 border-2 border-transparent'
                                                                    }`}
                                                                    onClick={() => {
                                                                        // Update all skill properties in a single call to avoid stale closure issues
                                                                        if (nodeId) {
                                                                            onUpdate(nodeId, {
                                                                                ...nodeData,
                                                                                skillName: skill.name,
                                                                                skillCategory: skill.category,
                                                                                skillDescription: skill.description,
                                                                            });
                                                                        }
                                                                        setShowSkillDropdown(false);
                                                                        setSkillSearch('');
                                                                    }}
                                                                >
                                                                    <div className={`w-8 h-8 rounded-md ${colors.bg} flex items-center justify-center flex-shrink-0`}>
                                                                        <Toolbox className={`h-4 w-4 ${colors.text}`} weight="fill" />
                                                                    </div>
                                                                    <div className="flex-1 min-w-0">
                                                                        <div className={`font-medium text-sm ${isSelected ? colors.text : ''}`}>{skill.name}</div>
                                                                        <div className="text-xs text-muted-foreground line-clamp-2 mt-0.5">{skill.description}</div>
                                                                    </div>
                                                                    {isSelected && (
                                                                        <CheckCircle className={`h-5 w-5 ${colors.text} flex-shrink-0`} weight="fill" />
                                                                    )}
                                                                </button>
                                                            );
                                                        })}
                                                    </div>
                                                </div>
                                            );
                                        })}
                                        {filteredSkills.length === 0 && (
                                            <div className="p-6 text-center">
                                                <MagnifyingGlass className="h-8 w-8 text-muted-foreground/30 mx-auto mb-2" />
                                                <div className="text-sm text-muted-foreground">No skills found matching &quot;{skillSearch}&quot;</div>
                                            </div>
                                        )}
                                    </div>
                                </ScrollArea>
                            </div>
                        )}
                    </div>
                </div>

                {/* Skill Description Card */}
                {displaySkill && (
                    <div className={`p-4 rounded-xl border-2 ${categoryColors?.bg} ${categoryColors?.border}`}>
                        <div className="flex items-center gap-2 mb-2">
                            <Toolbox className={`h-4 w-4 ${categoryColors?.text}`} weight="fill" />
                            <span className={`text-xs font-semibold ${categoryColors?.text} uppercase tracking-wide`}>Selected Skill</span>
                        </div>
                        <div className="text-sm text-foreground">{displaySkill.description}</div>
                    </div>
                )}

                {/* Input Mappings */}
                <div className="space-y-3">
                    <div>
                        <Label className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Input Mappings</Label>
                        <p className="text-xs text-muted-foreground mt-1">Map workflow variables to skill inputs</p>
                    </div>
                    <div className="space-y-2">
                        {Object.entries(inputs).map(([key, value]) => (
                            <div key={key} className="flex items-center gap-2 p-2 bg-muted/30 rounded-lg">
                                <Input
                                    className="flex-1 h-8 text-sm"
                                    value={key}
                                    placeholder="Input name"
                                    onChange={(e) => {
                                        const newInputs = { ...inputs };
                                        delete newInputs[key];
                                        newInputs[e.target.value] = value;
                                        handleChange('inputs', newInputs);
                                    }}
                                />
                                <span className="text-muted-foreground font-mono">=</span>
                                <Input
                                    className="flex-1 h-8 text-sm font-mono"
                                    value={value}
                                    placeholder="{{variable}}"
                                    onChange={(e) => {
                                        handleChange('inputs', { ...inputs, [key]: e.target.value });
                                    }}
                                />
                                <Button
                                    size="icon"
                                    variant="ghost"
                                    className="h-8 w-8 text-muted-foreground hover:text-red-500"
                                    onClick={() => {
                                        const newInputs = { ...inputs };
                                        delete newInputs[key];
                                        handleChange('inputs', newInputs);
                                    }}
                                >
                                    <Trash className="h-3.5 w-3.5" />
                                </Button>
                            </div>
                        ))}
                        <Button
                            size="sm"
                            variant="outline"
                            className="w-full border-dashed"
                            onClick={() => handleChange('inputs', { ...inputs, '': '' })}
                        >
                            <Plus className="h-3.5 w-3.5 mr-1.5" />
                            Add Input Mapping
                        </Button>
                    </div>
                </div>
            </div>
        );
    };

    // ============ CONNECTOR NODE ============
    const renderConnectorNodeConfig = () => {
        const selectedConnectorId = nodeData.connectorId as string;
        const selectedConnector = availableConnectors.find(c => c.id === selectedConnectorId);
        const filteredConnectors = availableConnectors.filter(c =>
            c.name.toLowerCase().includes(connectorSearch.toLowerCase()) ||
            c.category.toLowerCase().includes(connectorSearch.toLowerCase())
        );
        const inputs = (nodeData.inputs as Record<string, string>) || {};

        return (
            <div className="space-y-4">
                {/* Node Label */}
                <div className="space-y-2">
                    <Label>Node Label</Label>
                    <Input
                        value={(nodeData.label as string) || ''}
                        onChange={(e) => handleChange('label', e.target.value)}
                        placeholder="Connector"
                    />
                </div>

                {/* Connector Selection */}
                <div className="space-y-2">
                    <Label>Select Connector</Label>
                    <div className="relative">
                        <div
                            className={`flex items-center gap-2 p-3 border rounded-lg cursor-pointer hover:border-primary/50 transition-colors ${selectedConnector ? 'bg-teal-50 border-teal-200' : 'bg-muted/30'}`}
                            onClick={() => setShowConnectorDropdown(!showConnectorDropdown)}
                        >
                            {selectedConnector ? (
                                <>
                                    <Plugs className="h-4 w-4 text-teal-600" weight="fill" />
                                    <div className="flex-1">
                                        <div className="font-medium text-sm">{selectedConnector.name}</div>
                                        <div className="text-xs text-muted-foreground">{selectedConnector.category}</div>
                                    </div>
                                    <CheckCircle className="h-4 w-4 text-teal-600" weight="fill" />
                                </>
                            ) : (
                                <>
                                    <Circle className="h-4 w-4 text-muted-foreground" />
                                    <span className="text-sm text-muted-foreground">Select a connector...</span>
                                    <CaretDown className="h-4 w-4 text-muted-foreground ml-auto" />
                                </>
                            )}
                        </div>

                        {showConnectorDropdown && (
                            <div className="absolute z-20 top-full left-0 right-0 mt-1 bg-background border rounded-lg shadow-lg max-h-64 overflow-hidden">
                                <div className="p-2 border-b">
                                    <div className="relative">
                                        <MagnifyingGlass className="absolute left-2 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                                        <Input
                                            value={connectorSearch}
                                            onChange={(e) => setConnectorSearch(e.target.value)}
                                            placeholder="Search connectors..."
                                            className="pl-8 h-8"
                                        />
                                    </div>
                                </div>
                                <ScrollArea className="h-64">
                                    <div className="p-1">
                                        {connectorsLoading ? (
                                            <div className="p-4 flex items-center justify-center">
                                                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                                            </div>
                                        ) : filteredConnectors.length === 0 ? (
                                            <div className="p-4 text-center text-sm text-muted-foreground">
                                                No connectors found
                                            </div>
                                        ) : (
                                            filteredConnectors.map((connector) => {
                                                const colors = CONNECTOR_TYPE_COLORS[connector.category] || CONNECTOR_TYPE_COLORS.custom;
                                                const isEnabled = (connector as MarketplaceConnector & { is_enabled?: boolean }).is_enabled;
                                                const isSelected = selectedConnectorId === connector.id;
                                                return (
                                                    <div
                                                        key={connector.id}
                                                        className={`w-full text-left p-2 rounded hover:bg-muted/50 flex items-start gap-2 ${isSelected ? 'bg-teal-50 border border-teal-200' : ''}`}
                                                    >
                                                        {/* Enabled checkbox indicator */}
                                                        <div className="mt-0.5">
                                                            {isEnabled ? (
                                                                <CheckCircle className="h-4 w-4 text-green-600" weight="fill" />
                                                            ) : (
                                                                <Circle className="h-4 w-4 text-muted-foreground" />
                                                            )}
                                                        </div>
                                                        <div
                                                            className="flex-1 min-w-0 cursor-pointer"
                                                            onClick={() => {
                                                                if (isEnabled) {
                                                                    // Select the connector if it's enabled
                                                                    onUpdate(nodeId, {
                                                                        ...nodeData,
                                                                        connectorId: connector.id,
                                                                        connectorName: connector.name,
                                                                        connectorType: connector.category === 'itsm' ? 'rest' : connector.category === 'communication' ? 'rest' : 'rest',
                                                                        hasCredentials: true,
                                                                    });
                                                                    setShowConnectorDropdown(false);
                                                                    setConnectorSearch('');
                                                                } else {
                                                                    // Open the enable dialog
                                                                    setConnectorToEnable(connector.id);
                                                                }
                                                            }}
                                                        >
                                                            <div className="flex items-center gap-2">
                                                                <span className="font-medium text-sm truncate">{connector.name}</span>
                                                                {!isEnabled && (
                                                                    <span className="text-xs px-1.5 py-0.5 bg-amber-100 text-amber-700 rounded">
                                                                        Not configured
                                                                    </span>
                                                                )}
                                                            </div>
                                                            <div className="text-xs text-muted-foreground line-clamp-1">{connector.description}</div>
                                                            <div className="flex items-center gap-2 mt-1">
                                                                <span className={`inline-block px-1.5 py-0.5 rounded text-xs ${colors.bg} ${colors.text}`}>
                                                                    {connector.category}
                                                                </span>
                                                            </div>
                                                        </div>
                                                        {/* Connect button for non-enabled connectors */}
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
                                                                <Link className="h-3 w-3 mr-1" />
                                                                Connect
                                                            </Button>
                                                        )}
                                                    </div>
                                                );
                                            })
                                        )}
                                    </div>
                                </ScrollArea>
                            </div>
                        )}
                    </div>
                </div>

                {/* Operation */}
                <div className="space-y-2">
                    <Label>Operation</Label>
                    <Input
                        value={(nodeData.operation as string) || ''}
                        onChange={(e) => handleChange('operation', e.target.value)}
                        placeholder="e.g., getRecords, createIncident"
                    />
                </div>

                {/* Query / Request Body */}
                <PromptEditor
                    label="Query / Request"
                    value={(nodeData.query as string) || ''}
                    onChange={(v) => handleChange('query', v)}
                    placeholder="Enter query or request body..."
                    nodes={nodes}
                    currentNodeId={nodeId}
                    minHeight="80px"
                />

                {/* Input Mappings */}
                <div className="space-y-2">
                    <Label>Input Mappings</Label>
                    <div className="space-y-2">
                        {Object.entries(inputs).map(([key, value]) => (
                            <div key={key} className="flex items-center gap-2">
                                <Input
                                    className="flex-1"
                                    value={key}
                                    placeholder="Parameter"
                                    onChange={(e) => {
                                        const newInputs = { ...inputs };
                                        delete newInputs[key];
                                        newInputs[e.target.value] = value;
                                        handleChange('inputs', newInputs);
                                    }}
                                />
                                <span className="text-muted-foreground">=</span>
                                <Input
                                    className="flex-1"
                                    value={value}
                                    placeholder="{{variable}}"
                                    onChange={(e) => {
                                        handleChange('inputs', { ...inputs, [key]: e.target.value });
                                    }}
                                />
                                <Button
                                    size="icon"
                                    variant="ghost"
                                    className="h-8 w-8"
                                    onClick={() => {
                                        const newInputs = { ...inputs };
                                        delete newInputs[key];
                                        handleChange('inputs', newInputs);
                                    }}
                                >
                                    <Trash className="h-3 w-3 text-red-500" />
                                </Button>
                            </div>
                        ))}
                        <Button
                            size="sm"
                            variant="outline"
                            className="w-full"
                            onClick={() => handleChange('inputs', { ...inputs, '': '' })}
                        >
                            <Plus className="h-3 w-3 mr-1" />
                            Add Parameter
                        </Button>
                    </div>
                </div>
            </div>
        );
    };

    // ============ AGENT NODE ============
    const renderAgentNodeConfig = () => {
        const selectedAgentId = nodeData.agentId as string;
        const selectedAgent = availableAgents.find(a => a.id === selectedAgentId);
        const filteredAgents = availableAgents.filter(a =>
            a.name.toLowerCase().includes(agentSearch.toLowerCase()) ||
            a.type.toLowerCase().includes(agentSearch.toLowerCase())
        );

        return (
            <div className="space-y-4">
                {/* Node Label */}
                <div className="space-y-2">
                    <Label>Node Label</Label>
                    <Input
                        value={(nodeData.label as string) || ''}
                        onChange={(e) => handleChange('label', e.target.value)}
                        placeholder="Agent"
                    />
                </div>

                {/* Agent Selection */}
                <div className="space-y-2">
                    <Label>Select Agent</Label>
                    <div className="relative">
                        <div
                            className={`flex items-center gap-2 p-3 border rounded-lg cursor-pointer hover:border-primary/50 transition-colors ${selectedAgent ? 'bg-indigo-50 border-indigo-200' : 'bg-muted/30'}`}
                            onClick={() => setShowAgentDropdown(!showAgentDropdown)}
                        >
                            {selectedAgent ? (
                                <>
                                    <Robot className="h-4 w-4 text-indigo-600" weight="fill" />
                                    <div className="flex-1">
                                        <div className="font-medium text-sm">{selectedAgent.name}</div>
                                        <div className="text-xs text-muted-foreground">{selectedAgent.type}</div>
                                    </div>
                                    <CheckCircle className="h-4 w-4 text-indigo-600" weight="fill" />
                                </>
                            ) : (
                                <>
                                    <Circle className="h-4 w-4 text-muted-foreground" />
                                    <span className="text-sm text-muted-foreground">Select an agent...</span>
                                    <CaretDown className="h-4 w-4 text-muted-foreground ml-auto" />
                                </>
                            )}
                        </div>

                        {showAgentDropdown && (
                            <div className="absolute z-20 top-full left-0 right-0 mt-1 bg-background border rounded-lg shadow-lg max-h-64 overflow-hidden">
                                <div className="p-2 border-b">
                                    <div className="relative">
                                        <MagnifyingGlass className="absolute left-2 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                                        <Input
                                            value={agentSearch}
                                            onChange={(e) => setAgentSearch(e.target.value)}
                                            placeholder="Search agents..."
                                            className="pl-8 h-8"
                                        />
                                    </div>
                                </div>
                                <ScrollArea className="h-48">
                                    <div className="p-1">
                                        {filteredAgents.map((agent) => (
                                            <button
                                                key={agent.id}
                                                className={`w-full text-left p-2 rounded hover:bg-muted/50 flex items-start gap-2 ${selectedAgentId === agent.id ? 'bg-indigo-50' : ''}`}
                                                onClick={() => {
                                                    // Batch all agent data updates in a single call to avoid race conditions
                                                    onUpdate(nodeId, {
                                                        ...nodeData,
                                                        agentId: agent.id,
                                                        agentName: agent.name,
                                                        agentType: agent.type,
                                                        model: agent.model,
                                                        skills: agent.skills,
                                                        connectors: agent.connectors,
                                                    });
                                                    setShowAgentDropdown(false);
                                                    setAgentSearch('');
                                                }}
                                            >
                                                <Robot className="h-4 w-4 mt-0.5 text-indigo-600" />
                                                <div className="flex-1 min-w-0">
                                                    <div className="font-medium text-sm truncate">{agent.name}</div>
                                                    <div className="text-xs text-muted-foreground line-clamp-1">{agent.description}</div>
                                                    <div className="flex items-center gap-2 mt-1">
                                                        {agent.skills && agent.skills.length > 0 && (
                                                            <span className="flex items-center gap-0.5 text-xs text-green-600">
                                                                <Sparkle className="h-3 w-3" />
                                                                {agent.skills.length}
                                                            </span>
                                                        )}
                                                        {agent.connectors && agent.connectors.length > 0 && (
                                                            <span className="flex items-center gap-0.5 text-xs text-blue-600">
                                                                <Plugs className="h-3 w-3" />
                                                                {agent.connectors.length}
                                                            </span>
                                                        )}
                                                    </div>
                                                </div>
                                            </button>
                                        ))}
                                        {filteredAgents.length === 0 && (
                                            <div className="p-4 text-center text-sm text-muted-foreground">
                                                No agents found
                                            </div>
                                        )}
                                    </div>
                                </ScrollArea>
                            </div>
                        )}
                    </div>
                </div>

                {/* User Prompt Override */}
                <PromptEditor
                    label="User Prompt"
                    value={(nodeData.userPrompt as string) || ''}
                    onChange={(v) => handleChange('userPrompt', v)}
                    placeholder="Override or extend the agent's default prompt..."
                    nodes={nodes}
                    currentNodeId={nodeId}
                    minHeight="80px"
                />

                {/* Model Override */}
                <ModelSelector
                    value={(nodeData.model as string) || selectedAgent?.model || ''}
                    onChange={(v) => handleChange('model', v)}
                />

                {/* Connectors Section */}
                <div className="space-y-2">
                    <Label className="flex items-center gap-2">
                        <Plugs className="h-4 w-4 text-teal-600" />
                        Connectors
                    </Label>
                    <div className="border rounded-lg p-2 max-h-48 overflow-y-auto space-y-1">
                        {connectorsLoading ? (
                            <div className="p-3 flex items-center justify-center">
                                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                            </div>
                        ) : availableConnectors.length === 0 ? (
                            <div className="p-3 text-center text-sm text-muted-foreground">
                                No connectors available
                            </div>
                        ) : (
                            availableConnectors.map((connector) => {
                                const isEnabled = (connector as MarketplaceConnector & { is_enabled?: boolean }).is_enabled;
                                const selectedConnectors = (nodeData.connectors as string[]) || [];
                                const isSelected = selectedConnectors.includes(connector.id);

                                return (
                                    <div
                                        key={connector.id}
                                        className={`flex items-center gap-2 p-2 rounded hover:bg-muted/50 ${isSelected ? 'bg-teal-50' : ''}`}
                                    >
                                        {/* Checkbox for selection */}
                                        <input
                                            type="checkbox"
                                            checked={isSelected}
                                            disabled={!isEnabled}
                                            onChange={(e) => {
                                                if (e.target.checked) {
                                                    handleChange('connectors', [...selectedConnectors, connector.id]);
                                                } else {
                                                    handleChange('connectors', selectedConnectors.filter(c => c !== connector.id));
                                                }
                                            }}
                                            className="h-4 w-4 rounded border-gray-300 text-teal-600 focus:ring-teal-500 disabled:opacity-50"
                                        />
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2">
                                                <span className="text-sm font-medium truncate">{connector.name}</span>
                                                {isEnabled ? (
                                                    <CheckCircle className="h-3 w-3 text-green-600 shrink-0" weight="fill" />
                                                ) : (
                                                    <span className="text-xs px-1 py-0.5 bg-amber-100 text-amber-700 rounded shrink-0">
                                                        Not configured
                                                    </span>
                                                )}
                                            </div>
                                            <div className="text-xs text-muted-foreground truncate">
                                                {connector.description}
                                            </div>
                                        </div>
                                        {/* Connect button for non-enabled connectors */}
                                        {!isEnabled && (
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                className="h-6 text-xs shrink-0"
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
                            })
                        )}
                    </div>
                    <p className="text-xs text-muted-foreground">
                        Select connectors to make available to this agent
                    </p>
                </div>

                {/* Agent Info Card */}
                {selectedAgent && (
                    <div className="p-3 bg-indigo-50 rounded-lg border border-indigo-200 space-y-2">
                        <div className="text-xs font-medium text-indigo-700">Agent Configuration</div>
                        <div className="text-xs text-muted-foreground">{selectedAgent.description}</div>
                        <div className="flex flex-wrap gap-2 pt-1">
                            {selectedAgent.skills?.map((skill) => (
                                <span key={skill} className="px-1.5 py-0.5 bg-green-100 text-green-700 rounded text-xs">
                                    {skill}
                                </span>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        );
    };

    // ============ DATA NODE ============
    const renderDataNodeConfig = () => {
        const selectedConnectorId = nodeData.connectorId as string;
        const selectedConnector = availableConnectors.find(c => c.id === selectedConnectorId);

        return (
            <div className="space-y-4">
                {/* Node Label */}
                <div className="space-y-2">
                    <Label>Node Label</Label>
                    <Input
                        value={(nodeData.label as string) || ''}
                        onChange={(e) => handleChange('label', e.target.value)}
                        placeholder="Data Retrieval"
                    />
                </div>

                {/* Data Source */}
                <div className="space-y-2">
                    <Label>Data Source</Label>
                    <select
                        className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                        value={selectedConnectorId || ''}
                        onChange={(e) => {
                            const connector = availableConnectors.find(c => c.id === e.target.value);
                            // Batch both updates in a single call to avoid race conditions
                            onUpdate(nodeId, {
                                ...nodeData,
                                connectorId: e.target.value,
                                connectorName: connector?.name || '',
                            });
                        }}
                    >
                        <option value="">Select data source...</option>
                        {availableConnectors.map((connector) => (
                            <option key={connector.id} value={connector.id}>
                                {connector.name}
                            </option>
                        ))}
                    </select>
                </div>

                {/* Query */}
                <PromptEditor
                    label="Query"
                    value={(nodeData.query as string) || ''}
                    onChange={(v) => handleChange('query', v)}
                    placeholder="Enter data query or use {{variables}}..."
                    nodes={nodes}
                    currentNodeId={nodeId}
                    minHeight="100px"
                />

                {/* Output Format */}
                <div className="space-y-2">
                    <Label>Output Format</Label>
                    <select
                        className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                        value={(nodeData.outputFormat as string) || 'json'}
                        onChange={(e) => handleChange('outputFormat', e.target.value)}
                    >
                        <option value="json">JSON</option>
                        <option value="csv">CSV</option>
                        <option value="table">Table</option>
                        <option value="raw">Raw</option>
                    </select>
                </div>

                {/* Caching */}
                <div className="flex items-center justify-between">
                    <div>
                        <Label>Enable Caching</Label>
                        <p className="text-xs text-muted-foreground">Cache results for 5 minutes</p>
                    </div>
                    <Switch
                        checked={(nodeData.enableCache as boolean) || false}
                        onCheckedChange={(checked) => handleChange('enableCache', checked)}
                    />
                </div>

                {/* Info */}
                {selectedConnector && (
                    <div className="p-3 bg-blue-50 rounded-lg border border-blue-200">
                        <div className="flex items-center gap-2 text-xs font-medium text-blue-700">
                            <Database className="h-3 w-3" />
                            {selectedConnector.name}
                        </div>
                        <div className="text-xs text-muted-foreground mt-1">{selectedConnector.description}</div>
                    </div>
                )}
            </div>
        );
    };

    // ============ ANALYSIS NODE ============
    const renderAnalysisNodeConfig = () => {
        const selectedTypes = (nodeData.analysisTypes as string[]) || [];

        return (
            <div className="space-y-4">
                {/* Node Label */}
                <div className="space-y-2">
                    <Label>Node Label</Label>
                    <Input
                        value={(nodeData.label as string) || ''}
                        onChange={(e) => handleChange('label', e.target.value)}
                        placeholder="Analysis"
                    />
                </div>

                {/* Ticker / Symbol */}
                <PromptEditor
                    label="Ticker / Symbol"
                    value={(nodeData.ticker as string) || ''}
                    onChange={(v) => handleChange('ticker', v)}
                    placeholder="Enter ticker symbol or {{variable}}"
                    nodes={nodes}
                    currentNodeId={nodeId}
                    minHeight="40px"
                />

                {/* Analysis Types */}
                <div className="space-y-2">
                    <Label>Analysis Types</Label>
                    <div className="space-y-2">
                        {ANALYSIS_TYPES.map((type) => {
                            const isSelected = selectedTypes.includes(type.id);
                            return (
                                <button
                                    key={type.id}
                                    className={`w-full text-left p-3 rounded-lg border transition-colors ${isSelected ? 'bg-green-50 border-green-300' : 'hover:bg-muted/50'}`}
                                    onClick={() => {
                                        if (isSelected) {
                                            handleChange('analysisTypes', selectedTypes.filter(t => t !== type.id));
                                        } else {
                                            handleChange('analysisTypes', [...selectedTypes, type.id]);
                                        }
                                    }}
                                >
                                    <div className="flex items-center justify-between">
                                        <div>
                                            <div className="font-medium text-sm">{type.name}</div>
                                            <div className="text-xs text-muted-foreground">{type.description}</div>
                                        </div>
                                        {isSelected ? (
                                            <CheckCircle className="h-5 w-5 text-green-600" weight="fill" />
                                        ) : (
                                            <Circle className="h-5 w-5 text-muted-foreground" />
                                        )}
                                    </div>
                                </button>
                            );
                        })}
                    </div>
                </div>

                {/* Time Period */}
                <div className="space-y-2">
                    <Label>Time Period</Label>
                    <select
                        className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                        value={(nodeData.timePeriod as string) || '1y'}
                        onChange={(e) => handleChange('timePeriod', e.target.value)}
                    >
                        <option value="1d">1 Day</option>
                        <option value="1w">1 Week</option>
                        <option value="1m">1 Month</option>
                        <option value="3m">3 Months</option>
                        <option value="6m">6 Months</option>
                        <option value="1y">1 Year</option>
                        <option value="5y">5 Years</option>
                    </select>
                </div>

                {/* Include Recommendation */}
                <div className="flex items-center justify-between">
                    <div>
                        <Label>Generate Recommendation</Label>
                        <p className="text-xs text-muted-foreground">Buy/Sell/Hold with confidence</p>
                    </div>
                    <Switch
                        checked={(nodeData.generateRecommendation as boolean) ?? true}
                        onCheckedChange={(checked) => handleChange('generateRecommendation', checked)}
                    />
                </div>
            </div>
        );
    };

    // ============ COMPLIANCE NODE ============
    const renderComplianceNodeConfig = () => {
        const selectedChecks = (nodeData.checkTypes as string[]) || [];

        return (
            <div className="space-y-4">
                {/* Node Label */}
                <div className="space-y-2">
                    <Label>Node Label</Label>
                    <Input
                        value={(nodeData.label as string) || ''}
                        onChange={(e) => handleChange('label', e.target.value)}
                        placeholder="Compliance Check"
                    />
                </div>

                {/* Input Data */}
                <PromptEditor
                    label="Input Data"
                    value={(nodeData.inputData as string) || ''}
                    onChange={(v) => handleChange('inputData', v)}
                    placeholder="Select data to check for compliance..."
                    nodes={nodes}
                    currentNodeId={nodeId}
                    minHeight="60px"
                />

                {/* Compliance Checks */}
                <div className="space-y-2">
                    <Label>Compliance Checks</Label>
                    <div className="space-y-2">
                        {COMPLIANCE_CHECK_TYPES.map((check) => {
                            const isSelected = selectedChecks.includes(check.id);
                            return (
                                <button
                                    key={check.id}
                                    className={`w-full text-left p-3 rounded-lg border transition-colors ${isSelected ? 'bg-purple-50 border-purple-300' : 'hover:bg-muted/50'}`}
                                    onClick={() => {
                                        if (isSelected) {
                                            handleChange('checkTypes', selectedChecks.filter(t => t !== check.id));
                                        } else {
                                            handleChange('checkTypes', [...selectedChecks, check.id]);
                                        }
                                    }}
                                >
                                    <div className="flex items-center justify-between">
                                        <div>
                                            <div className="font-medium text-sm">{check.name}</div>
                                            <div className="text-xs text-muted-foreground">{check.description}</div>
                                        </div>
                                        {isSelected ? (
                                            <CheckCircle className="h-5 w-5 text-purple-600" weight="fill" />
                                        ) : (
                                            <Circle className="h-5 w-5 text-muted-foreground" />
                                        )}
                                    </div>
                                </button>
                            );
                        })}
                    </div>
                </div>

                {/* Risk Threshold */}
                <div className="space-y-2">
                    <Label>Risk Threshold</Label>
                    <select
                        className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                        value={(nodeData.riskThreshold as string) || 'medium'}
                        onChange={(e) => handleChange('riskThreshold', e.target.value)}
                    >
                        <option value="low">Low - Flag any concerns</option>
                        <option value="medium">Medium - Flag significant issues</option>
                        <option value="high">High - Only critical violations</option>
                    </select>
                </div>

                {/* Fail on Violation */}
                <div className="flex items-center justify-between">
                    <div>
                        <Label>Fail on Violation</Label>
                        <p className="text-xs text-muted-foreground">Stop workflow on compliance failure</p>
                    </div>
                    <Switch
                        checked={(nodeData.failOnViolation as boolean) ?? true}
                        onCheckedChange={(checked) => handleChange('failOnViolation', checked)}
                    />
                </div>

                {/* Warning Box */}
                <div className="p-3 bg-amber-50 rounded-lg border border-amber-200">
                    <div className="flex items-center gap-2 text-xs font-medium text-amber-700">
                        <Warning className="h-4 w-4" />
                        Compliance Notice
                    </div>
                    <div className="text-xs text-amber-600 mt-1">
                        Results should be reviewed by qualified compliance personnel. This node provides guidance, not legal advice.
                    </div>
                </div>
            </div>
        );
    };

    // ============ RESEARCH NODE ============
    const renderResearchNodeConfig = () => {
        const selectedDocs = (nodeData.documentIds as string[]) || [];

        return (
            <div className="space-y-4">
                {/* Node Label */}
                <div className="space-y-2">
                    <Label>Node Label</Label>
                    <Input
                        value={(nodeData.label as string) || ''}
                        onChange={(e) => handleChange('label', e.target.value)}
                        placeholder="Research"
                    />
                </div>

                {/* Research Query */}
                <PromptEditor
                    label="Research Query"
                    value={(nodeData.query as string) || ''}
                    onChange={(v) => handleChange('query', v)}
                    placeholder="What would you like to research?"
                    nodes={nodes}
                    currentNodeId={nodeId}
                    minHeight="80px"
                />

                {/* Document Sources */}
                <div className="space-y-2">
                    <Label>Document Sources</Label>
                    <div className="space-y-2">
                        {selectedDocs.map((docId, i) => (
                            <div key={i} className="flex items-center justify-between p-2 border rounded bg-muted/50">
                                <span className="text-sm">{docId}</span>
                                <Button
                                    size="icon"
                                    variant="ghost"
                                    className="h-6 w-6"
                                    onClick={() => handleChange('documentIds', selectedDocs.filter((_, idx) => idx !== i))}
                                >
                                    <X className="h-3 w-3" />
                                </Button>
                            </div>
                        ))}
                        <Button
                            size="sm"
                            variant="outline"
                            className="w-full"
                            onClick={() => handleChange('documentIds', [...selectedDocs, `doc-${Date.now()}`])}
                        >
                            <Plus className="h-3 w-3 mr-1" />
                            Add Document Source
                        </Button>
                    </div>
                </div>

                {/* Knowledge Bases */}
                <div className="space-y-2">
                    <Label>Knowledge Bases</Label>
                    <div className="space-y-2">
                        {kbLoading ? (
                            <div className="flex items-center gap-2 text-muted-foreground p-2">
                                <Loader2 className="h-4 w-4 animate-spin" />
                                Loading knowledge bases...
                            </div>
                        ) : knowledgeBases.length === 0 ? (
                            <div className="text-muted-foreground text-sm p-2">
                                No knowledge bases found. Upload documents to create one.
                            </div>
                        ) : (
                            knowledgeBases.map((kb) => {
                                const isSelected = ((nodeData.knowledgeBases as string[]) || []).includes(kb.id);
                                return (
                                    <button
                                        key={kb.id}
                                        className={`w-full text-left p-2 rounded-lg border transition-colors flex items-center justify-between ${isSelected ? 'bg-orange-50 border-orange-300' : 'hover:bg-muted/50'}`}
                                        onClick={() => {
                                            const current = (nodeData.knowledgeBases as string[]) || [];
                                            if (isSelected) {
                                                handleChange('knowledgeBases', current.filter(id => id !== kb.id));
                                            } else {
                                                handleChange('knowledgeBases', [...current, kb.id]);
                                            }
                                        }}
                                    >
                                        <span className="text-sm">{kb.name}</span>
                                        {isSelected ? (
                                            <CheckCircle className="h-4 w-4 text-orange-600" weight="fill" />
                                        ) : (
                                            <Circle className="h-4 w-4 text-muted-foreground" />
                                        )}
                                    </button>
                                );
                            })
                        )}
                    </div>
                </div>

                {/* Search Settings */}
                <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <Label className="text-xs text-muted-foreground">Max Results</Label>
                        <Input
                            type="number"
                            min="1"
                            max="50"
                            value={(nodeData.maxResults as number) || 10}
                            onChange={(e) => handleChange('maxResults', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <Label className="text-xs text-muted-foreground">Min Confidence</Label>
                        <Input
                            type="number"
                            min="0"
                            max="1"
                            step="0.1"
                            value={(nodeData.minConfidence as number) || 0.7}
                            onChange={(e) => handleChange('minConfidence', parseFloat(e.target.value))}
                        />
                    </div>
                </div>

                {/* Include Citations */}
                <div className="flex items-center justify-between">
                    <div>
                        <Label>Include Citations</Label>
                        <p className="text-xs text-muted-foreground">Add source references to output</p>
                    </div>
                    <Switch
                        checked={(nodeData.includeCitations as boolean) ?? true}
                        onCheckedChange={(checked) => handleChange('includeCitations', checked)}
                    />
                </div>
            </div>
        );
    };

    // ============ WAIT NODE ============
    const renderWaitConfig = () => (
        <div className="space-y-4">
            <div className="space-y-2">
                <Label>Node Label</Label>
                <Input
                    value={(nodeData.label as string) || ''}
                    onChange={(e) => handleChange('label', e.target.value)}
                    placeholder="Wait"
                />
            </div>

            <div className="space-y-2">
                <Label>Wait Type</Label>
                <select
                    className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                    value={(nodeData.waitType as string) || 'duration'}
                    onChange={(e) => handleChange('waitType', e.target.value)}
                >
                    <option value="duration">Fixed Duration</option>
                    <option value="until_time">Until Specific Time</option>
                    <option value="business_hours">Business Hours Only</option>
                </select>
            </div>

            {(nodeData.waitType as string) === 'duration' || !nodeData.waitType ? (
                <div className="grid grid-cols-3 gap-2">
                    <div className="space-y-2">
                        <Label className="text-xs text-muted-foreground">Hours</Label>
                        <Input
                            type="number"
                            min="0"
                            value={(nodeData.hours as number) || 0}
                            onChange={(e) => handleChange('hours', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <Label className="text-xs text-muted-foreground">Minutes</Label>
                        <Input
                            type="number"
                            min="0"
                            max="59"
                            value={(nodeData.minutes as number) || 0}
                            onChange={(e) => handleChange('minutes', parseInt(e.target.value))}
                        />
                    </div>
                    <div className="space-y-2">
                        <Label className="text-xs text-muted-foreground">Seconds</Label>
                        <Input
                            type="number"
                            min="0"
                            max="59"
                            value={(nodeData.seconds as number) || 0}
                            onChange={(e) => handleChange('seconds', parseInt(e.target.value))}
                        />
                    </div>
                </div>
            ) : (nodeData.waitType as string) === 'until_time' ? (
                <div className="space-y-2">
                    <Label>Wait Until</Label>
                    <Input
                        type="datetime-local"
                        value={(nodeData.untilTime as string) || ''}
                        onChange={(e) => handleChange('untilTime', e.target.value)}
                    />
                </div>
            ) : (
                <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <Label className="text-xs text-muted-foreground">Start Hour</Label>
                            <Input
                                type="number"
                                min="0"
                                max="23"
                                value={(nodeData.businessStartHour as number) || 9}
                                onChange={(e) => handleChange('businessStartHour', parseInt(e.target.value))}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label className="text-xs text-muted-foreground">End Hour</Label>
                            <Input
                                type="number"
                                min="0"
                                max="23"
                                value={(nodeData.businessEndHour as number) || 17}
                                onChange={(e) => handleChange('businessEndHour', parseInt(e.target.value))}
                            />
                        </div>
                    </div>
                    <div className="space-y-2">
                        <Label className="text-xs text-muted-foreground">Timezone</Label>
                        <select
                            className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                            value={(nodeData.timezone as string) || 'UTC'}
                            onChange={(e) => handleChange('timezone', e.target.value)}
                        >
                            <option value="UTC">UTC</option>
                            <option value="America/New_York">Eastern Time</option>
                            <option value="America/Chicago">Central Time</option>
                            <option value="America/Denver">Mountain Time</option>
                            <option value="America/Los_Angeles">Pacific Time</option>
                            <option value="Europe/London">London</option>
                            <option value="Asia/Tokyo">Tokyo</option>
                        </select>
                    </div>
                </div>
            )}

            <div className="p-3 bg-indigo-50 rounded-lg border border-indigo-200">
                <div className="text-xs text-indigo-700">
                    <Timer className="inline h-3 w-3 mr-1" />
                    Workflow will pause at this step until the wait condition is met.
                </div>
            </div>
        </div>
    );

    // ============ VARIABLE ASSIGNER NODE ============
    const renderVariableAssignerConfig = () => {
        const assignments = (nodeData.assignments as Array<{ variable: string; value: string; type: string }>) || [];

        return (
            <div className="space-y-4">
                <div className="space-y-2">
                    <Label>Node Label</Label>
                    <Input
                        value={(nodeData.label as string) || ''}
                        onChange={(e) => handleChange('label', e.target.value)}
                        placeholder="Variable Assigner"
                    />
                </div>

                <div className="space-y-2">
                    <Label>Variable Assignments</Label>
                    <div className="space-y-2">
                        {assignments.map((assignment, i) => (
                            <div key={i} className="p-3 border rounded-lg space-y-2 bg-muted/20">
                                <div className="flex items-center gap-2">
                                    <Input
                                        className="flex-1"
                                        value={assignment.variable}
                                        placeholder="Variable name"
                                        onChange={(e) => {
                                            const newAssignments = [...assignments];
                                            newAssignments[i] = { ...newAssignments[i], variable: e.target.value };
                                            handleChange('assignments', newAssignments);
                                        }}
                                    />
                                    <select
                                        className="h-9 px-2 rounded-md border bg-background text-sm"
                                        value={assignment.type || 'string'}
                                        onChange={(e) => {
                                            const newAssignments = [...assignments];
                                            newAssignments[i] = { ...newAssignments[i], type: e.target.value };
                                            handleChange('assignments', newAssignments);
                                        }}
                                    >
                                        <option value="string">String</option>
                                        <option value="number">Number</option>
                                        <option value="boolean">Boolean</option>
                                        <option value="json">JSON</option>
                                        <option value="array">Array</option>
                                    </select>
                                    <Button
                                        size="icon"
                                        variant="ghost"
                                        className="h-9 w-9"
                                        onClick={() => {
                                            handleChange('assignments', assignments.filter((_, idx) => idx !== i));
                                        }}
                                    >
                                        <Trash className="h-4 w-4 text-red-500" />
                                    </Button>
                                </div>
                                <Input
                                    value={assignment.value}
                                    placeholder="Value or {{variable}}"
                                    onChange={(e) => {
                                        const newAssignments = [...assignments];
                                        newAssignments[i] = { ...newAssignments[i], value: e.target.value };
                                        handleChange('assignments', newAssignments);
                                    }}
                                />
                            </div>
                        ))}
                        <Button
                            size="sm"
                            variant="outline"
                            className="w-full"
                            onClick={() => {
                                handleChange('assignments', [...assignments, { variable: '', value: '', type: 'string' }]);
                            }}
                        >
                            <Plus className="h-3 w-3 mr-1" />
                            Add Assignment
                        </Button>
                    </div>
                </div>
            </div>
        );
    };

    // ============ LIST OPERATOR NODE ============
    const renderListOperatorConfig = () => (
        <div className="space-y-4">
            <div className="space-y-2">
                <Label>Node Label</Label>
                <Input
                    value={(nodeData.label as string) || ''}
                    onChange={(e) => handleChange('label', e.target.value)}
                    placeholder="List Operator"
                />
            </div>

            <div className="space-y-2">
                <Label>Operation</Label>
                <select
                    className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                    value={(nodeData.operation as string) || 'first'}
                    onChange={(e) => handleChange('operation', e.target.value)}
                >
                    <optgroup label="Access">
                        <option value="first">First Item</option>
                        <option value="last">Last Item</option>
                        <option value="at">At Index</option>
                        <option value="slice">Slice</option>
                        <option value="length">Length</option>
                    </optgroup>
                    <optgroup label="Transform">
                        <option value="filter">Filter</option>
                        <option value="map">Map</option>
                        <option value="reduce">Reduce</option>
                        <option value="flatten">Flatten</option>
                        <option value="unique">Unique</option>
                    </optgroup>
                    <optgroup label="Combine">
                        <option value="concat">Concatenate</option>
                        <option value="merge">Merge</option>
                        <option value="zip">Zip</option>
                    </optgroup>
                    <optgroup label="Sort & Search">
                        <option value="sort">Sort</option>
                        <option value="reverse">Reverse</option>
                        <option value="find">Find</option>
                        <option value="includes">Includes</option>
                    </optgroup>
                </select>
            </div>

            <PromptEditor
                label="Input List"
                value={(nodeData.inputList as string) || ''}
                onChange={(v) => handleChange('inputList', v)}
                placeholder="Select list variable..."
                nodes={nodes}
                currentNodeId={nodeId}
                minHeight="60px"
            />

            {['at', 'slice'].includes((nodeData.operation as string) || '') && (
                <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <Label className="text-xs text-muted-foreground">Start Index</Label>
                        <Input
                            type="number"
                            value={(nodeData.startIndex as number) || 0}
                            onChange={(e) => handleChange('startIndex', parseInt(e.target.value))}
                        />
                    </div>
                    {(nodeData.operation as string) === 'slice' && (
                        <div className="space-y-2">
                            <Label className="text-xs text-muted-foreground">End Index</Label>
                            <Input
                                type="number"
                                value={(nodeData.endIndex as number) || 0}
                                onChange={(e) => handleChange('endIndex', parseInt(e.target.value))}
                            />
                        </div>
                    )}
                </div>
            )}

            {['filter', 'find', 'includes'].includes((nodeData.operation as string) || '') && (
                <div className="space-y-2">
                    <Label>Condition</Label>
                    <Input
                        value={(nodeData.condition as string) || ''}
                        onChange={(e) => handleChange('condition', e.target.value)}
                        placeholder="e.g., item > 10 or item.status === 'active'"
                    />
                </div>
            )}

            {(nodeData.operation as string) === 'sort' && (
                <div className="space-y-2">
                    <Label>Sort Key (optional)</Label>
                    <Input
                        value={(nodeData.sortKey as string) || ''}
                        onChange={(e) => handleChange('sortKey', e.target.value)}
                        placeholder="e.g., 'name' or 'date'"
                    />
                    <div className="flex items-center justify-between mt-2">
                        <Label className="text-xs text-muted-foreground">Descending</Label>
                        <Switch
                            checked={(nodeData.descending as boolean) || false}
                            onCheckedChange={(checked) => handleChange('descending', checked)}
                        />
                    </div>
                </div>
            )}
        </div>
    );

    // ============ NOTES NODE ============
    const renderNotesConfig = () => (
        <div className="space-y-4">
            <div className="space-y-2">
                <Label>Note Title</Label>
                <Input
                    value={(nodeData.title as string) || ''}
                    onChange={(e) => handleChange('title', e.target.value)}
                    placeholder="Add a title..."
                />
            </div>

            <div className="space-y-2">
                <Label>Content</Label>
                <textarea
                    className="w-full min-h-[150px] rounded-md border px-3 py-2 text-sm bg-background resize-none"
                    value={(nodeData.content as string) || ''}
                    onChange={(e) => handleChange('content', e.target.value)}
                    placeholder="Write your notes here..."
                />
            </div>

            <div className="space-y-2">
                <Label>Color</Label>
                <div className="flex gap-2">
                    {['yellow', 'blue', 'green', 'pink', 'purple'].map((color) => (
                        <button
                            key={color}
                            className={`w-8 h-8 rounded-lg border-2 transition-all ${
                                (nodeData.color as string) === color
                                    ? 'border-gray-900 scale-110'
                                    : 'border-transparent'
                            } bg-${color}-200`}
                            style={{ backgroundColor: `var(--${color}-200, ${color})` }}
                            onClick={() => handleChange('color', color)}
                        />
                    ))}
                </div>
            </div>

            <div className="p-3 bg-yellow-50 rounded-lg border border-yellow-200">
                <div className="text-xs text-yellow-700">
                    <Note className="inline h-3 w-3 mr-1" />
                    Notes are for documentation only and do not affect workflow execution.
                </div>
            </div>
        </div>
    );

    // ============ DOCUMENT EXTRACTOR NODE ============
    const renderDocumentExtractorConfig = () => (
        <div className="space-y-4">
            <div className="space-y-2">
                <Label>Node Label</Label>
                <Input
                    value={(nodeData.label as string) || ''}
                    onChange={(e) => handleChange('label', e.target.value)}
                    placeholder="Document Extractor"
                />
            </div>

            <PromptEditor
                label="Document Input"
                value={(nodeData.documentInput as string) || ''}
                onChange={(v) => handleChange('documentInput', v)}
                placeholder="Select document variable or path..."
                nodes={nodes}
                currentNodeId={nodeId}
                minHeight="60px"
            />

            <div className="space-y-2">
                <Label>Extraction Type</Label>
                <select
                    className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                    value={(nodeData.extractionType as string) || 'full_text'}
                    onChange={(e) => handleChange('extractionType', e.target.value)}
                >
                    <option value="full_text">Full Text</option>
                    <option value="structured">Structured Data</option>
                    <option value="sections">Sections</option>
                    <option value="tables">Tables Only</option>
                    <option value="metadata">Metadata Only</option>
                </select>
            </div>

            <div className="space-y-2">
                <Label>Supported Formats</Label>
                <div className="flex flex-wrap gap-1">
                    {['PDF', 'DOCX', 'TXT', 'HTML', 'MD', 'CSV', 'XLSX'].map((format) => (
                        <span key={format} className="px-2 py-1 bg-green-100 text-green-700 rounded text-xs">
                            {format}
                        </span>
                    ))}
                </div>
            </div>

            <div className="flex items-center justify-between">
                <div>
                    <Label>Preserve Formatting</Label>
                    <p className="text-xs text-muted-foreground">Keep original text formatting</p>
                </div>
                <Switch
                    checked={(nodeData.preserveFormatting as boolean) || false}
                    onCheckedChange={(checked) => handleChange('preserveFormatting', checked)}
                />
            </div>

            <div className="flex items-center justify-between">
                <div>
                    <Label>Extract Images</Label>
                    <p className="text-xs text-muted-foreground">Include embedded images</p>
                </div>
                <Switch
                    checked={(nodeData.extractImages as boolean) || false}
                    onCheckedChange={(checked) => handleChange('extractImages', checked)}
                />
            </div>
        </div>
    );

    // ============ PARAMETER EXTRACTOR NODE ============
    const renderParameterExtractorConfig = () => {
        const parameters = (nodeData.parameters as Array<{ name: string; type: string; description: string; required: boolean }>) || [];

        return (
            <div className="space-y-4">
                <div className="space-y-2">
                    <Label>Node Label</Label>
                    <Input
                        value={(nodeData.label as string) || ''}
                        onChange={(e) => handleChange('label', e.target.value)}
                        placeholder="Parameter Extractor"
                    />
                </div>

                <PromptEditor
                    label="Input Text"
                    value={(nodeData.inputText as string) || ''}
                    onChange={(v) => handleChange('inputText', v)}
                    placeholder="Text to extract parameters from..."
                    nodes={nodes}
                    currentNodeId={nodeId}
                    minHeight="80px"
                />

                <div className="space-y-2">
                    <Label>Extraction Method</Label>
                    <select
                        className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                        value={(nodeData.extractionMethod as string) || 'llm'}
                        onChange={(e) => handleChange('extractionMethod', e.target.value)}
                    >
                        <option value="llm">LLM-based (Intelligent)</option>
                        <option value="pattern">Pattern Matching (Regex)</option>
                        <option value="hybrid">Hybrid (Pattern + LLM)</option>
                    </select>
                </div>

                <div className="space-y-2">
                    <Label>Parameters to Extract</Label>
                    <div className="space-y-2">
                        {parameters.map((param, i) => (
                            <div key={i} className="p-3 border rounded-lg space-y-2 bg-muted/20">
                                <div className="flex items-center gap-2">
                                    <Input
                                        className="flex-1"
                                        value={param.name}
                                        placeholder="Parameter name"
                                        onChange={(e) => {
                                            const newParams = [...parameters];
                                            newParams[i] = { ...newParams[i], name: e.target.value };
                                            handleChange('parameters', newParams);
                                        }}
                                    />
                                    <select
                                        className="h-9 px-2 rounded-md border bg-background text-sm"
                                        value={param.type || 'string'}
                                        onChange={(e) => {
                                            const newParams = [...parameters];
                                            newParams[i] = { ...newParams[i], type: e.target.value };
                                            handleChange('parameters', newParams);
                                        }}
                                    >
                                        <option value="string">String</option>
                                        <option value="number">Number</option>
                                        <option value="date">Date</option>
                                        <option value="email">Email</option>
                                        <option value="phone">Phone</option>
                                        <option value="currency">Currency</option>
                                    </select>
                                    <Button
                                        size="icon"
                                        variant="ghost"
                                        className="h-9 w-9"
                                        onClick={() => {
                                            handleChange('parameters', parameters.filter((_, idx) => idx !== i));
                                        }}
                                    >
                                        <Trash className="h-4 w-4 text-red-500" />
                                    </Button>
                                </div>
                                <Input
                                    value={param.description || ''}
                                    placeholder="Description (helps LLM understand)"
                                    onChange={(e) => {
                                        const newParams = [...parameters];
                                        newParams[i] = { ...newParams[i], description: e.target.value };
                                        handleChange('parameters', newParams);
                                    }}
                                />
                                <div className="flex items-center gap-2">
                                    <input
                                        type="checkbox"
                                        id={`required-${i}`}
                                        checked={param.required || false}
                                        onChange={(e) => {
                                            const newParams = [...parameters];
                                            newParams[i] = { ...newParams[i], required: e.target.checked };
                                            handleChange('parameters', newParams);
                                        }}
                                        className="rounded"
                                    />
                                    <label htmlFor={`required-${i}`} className="text-xs text-muted-foreground">Required</label>
                                </div>
                            </div>
                        ))}
                        <Button
                            size="sm"
                            variant="outline"
                            className="w-full"
                            onClick={() => {
                                handleChange('parameters', [...parameters, { name: '', type: 'string', description: '', required: false }]);
                            }}
                        >
                            <Plus className="h-3 w-3 mr-1" />
                            Add Parameter
                        </Button>
                    </div>
                </div>

                {(nodeData.extractionMethod as string) === 'llm' && (
                    <ModelSelector
                        value={(nodeData.model as string) || ''}
                        onChange={(v) => handleChange('model', v)}
                    />
                )}
            </div>
        );
    };

    // ============ OCR NODE ============
    const renderOCRConfig = () => (
        <div className="space-y-4">
            <div className="space-y-2">
                <Label>Node Label</Label>
                <Input
                    value={(nodeData.label as string) || ''}
                    onChange={(e) => handleChange('label', e.target.value)}
                    placeholder="OCR"
                />
            </div>

            <PromptEditor
                label="Image Input"
                value={(nodeData.imageInput as string) || ''}
                onChange={(v) => handleChange('imageInput', v)}
                placeholder="Select image variable or path..."
                nodes={nodes}
                currentNodeId={nodeId}
                minHeight="60px"
            />

            <div className="space-y-2">
                <Label>OCR Provider</Label>
                <select
                    className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                    value={(nodeData.provider as string) || 'tesseract'}
                    onChange={(e) => handleChange('provider', e.target.value)}
                >
                    <option value="tesseract">Tesseract (Local)</option>
                    <option value="google_vision">Google Cloud Vision</option>
                    <option value="aws_textract">AWS Textract</option>
                    <option value="azure_cognitive">Azure Cognitive Services</option>
                </select>
            </div>

            <div className="space-y-2">
                <Label>Language</Label>
                <select
                    className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                    value={(nodeData.language as string) || 'eng'}
                    onChange={(e) => handleChange('language', e.target.value)}
                >
                    <option value="eng">English</option>
                    <option value="spa">Spanish</option>
                    <option value="fra">French</option>
                    <option value="deu">German</option>
                    <option value="chi_sim">Chinese (Simplified)</option>
                    <option value="jpn">Japanese</option>
                    <option value="kor">Korean</option>
                </select>
            </div>

            <div className="flex items-center justify-between">
                <div>
                    <Label>Detect Layout</Label>
                    <p className="text-xs text-muted-foreground">Preserve document structure</p>
                </div>
                <Switch
                    checked={(nodeData.detectLayout as boolean) ?? true}
                    onCheckedChange={(checked) => handleChange('detectLayout', checked)}
                />
            </div>

            <div className="flex items-center justify-between">
                <div>
                    <Label>Extract Tables</Label>
                    <p className="text-xs text-muted-foreground">Parse tabular data</p>
                </div>
                <Switch
                    checked={(nodeData.extractTables as boolean) || false}
                    onCheckedChange={(checked) => handleChange('extractTables', checked)}
                />
            </div>

            <div className="p-3 bg-sky-50 rounded-lg border border-sky-200">
                <div className="text-xs text-sky-700">
                    <Scan className="inline h-3 w-3 mr-1" />
                    Supports PNG, JPG, TIFF, PDF (first page), and BMP formats.
                </div>
            </div>
        </div>
    );

    // ============ NOTIFICATION NODE ============
    const renderNotificationConfig = () => (
        <div className="space-y-4">
            <div className="space-y-2">
                <Label>Node Label</Label>
                <Input
                    value={(nodeData.label as string) || ''}
                    onChange={(e) => handleChange('label', e.target.value)}
                    placeholder="Notification"
                />
            </div>

            <div className="space-y-2">
                <Label>Channel</Label>
                <select
                    className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                    value={(nodeData.channel as string) || 'email'}
                    onChange={(e) => handleChange('channel', e.target.value)}
                >
                    <option value="email">Email</option>
                    <option value="slack">Slack</option>
                    <option value="teams">Microsoft Teams</option>
                    <option value="sms">SMS</option>
                    <option value="webhook">Webhook</option>
                    <option value="in_app">In-App</option>
                </select>
            </div>

            <PromptEditor
                label="Recipients"
                value={(nodeData.recipients as string) || ''}
                onChange={(v) => handleChange('recipients', v)}
                placeholder="user@example.com or {{recipient_var}}"
                nodes={nodes}
                currentNodeId={nodeId}
                minHeight="40px"
            />

            <div className="space-y-2">
                <Label>Subject / Title</Label>
                <Input
                    value={(nodeData.subject as string) || ''}
                    onChange={(e) => handleChange('subject', e.target.value)}
                    placeholder="Notification subject..."
                />
            </div>

            <PromptEditor
                label="Message Body"
                value={(nodeData.messageBody as string) || ''}
                onChange={(v) => handleChange('messageBody', v)}
                placeholder="Write your notification message..."
                nodes={nodes}
                currentNodeId={nodeId}
                minHeight="100px"
            />

            <div className="space-y-2">
                <Label>Priority</Label>
                <select
                    className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                    value={(nodeData.priority as string) || 'normal'}
                    onChange={(e) => handleChange('priority', e.target.value)}
                >
                    <option value="low">Low</option>
                    <option value="normal">Normal</option>
                    <option value="high">High</option>
                    <option value="urgent">Urgent</option>
                </select>
            </div>

            <div className="flex items-center justify-between">
                <div>
                    <Label>Wait for Delivery</Label>
                    <p className="text-xs text-muted-foreground">Block until notification sent</p>
                </div>
                <Switch
                    checked={(nodeData.waitForDelivery as boolean) || false}
                    onCheckedChange={(checked) => handleChange('waitForDelivery', checked)}
                />
            </div>
        </div>
    );

    // ============ WEBHOOK TRIGGER NODE ============
    const renderWebhookTriggerConfig = () => (
        <div className="space-y-4">
            <div className="space-y-2">
                <Label>Node Label</Label>
                <Input
                    value={(nodeData.label as string) || ''}
                    onChange={(e) => handleChange('label', e.target.value)}
                    placeholder="Webhook Trigger"
                />
            </div>

            <div className="space-y-2">
                <Label>Webhook Path</Label>
                <div className="flex items-center gap-2">
                    <span className="text-sm text-muted-foreground">/webhooks/</span>
                    <Input
                        className="flex-1"
                        value={(nodeData.path as string) || ''}
                        onChange={(e) => handleChange('path', e.target.value)}
                        placeholder="my-webhook"
                    />
                </div>
            </div>

            <div className="space-y-2">
                <Label>HTTP Methods</Label>
                <div className="flex flex-wrap gap-2">
                    {['POST', 'GET', 'PUT', 'DELETE'].map((method) => {
                        const methods = (nodeData.methods as string[]) || ['POST'];
                        const isSelected = methods.includes(method);
                        return (
                            <button
                                key={method}
                                className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
                                    isSelected
                                        ? 'bg-orange-500 text-white'
                                        : 'bg-muted text-muted-foreground hover:bg-muted/80'
                                }`}
                                onClick={() => {
                                    if (isSelected) {
                                        handleChange('methods', methods.filter(m => m !== method));
                                    } else {
                                        handleChange('methods', [...methods, method]);
                                    }
                                }}
                            >
                                {method}
                            </button>
                        );
                    })}
                </div>
            </div>

            <div className="space-y-2">
                <Label>Authentication</Label>
                <select
                    className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                    value={(nodeData.authType as string) || 'none'}
                    onChange={(e) => handleChange('authType', e.target.value)}
                >
                    <option value="none">No Authentication</option>
                    <option value="api_key">API Key</option>
                    <option value="hmac">HMAC Signature</option>
                    <option value="basic">Basic Auth</option>
                </select>
            </div>

            {(nodeData.authType as string) === 'hmac' && (
                <div className="space-y-2">
                    <Label>HMAC Secret</Label>
                    <Input
                        type="password"
                        value={(nodeData.hmacSecret as string) || ''}
                        onChange={(e) => handleChange('hmacSecret', e.target.value)}
                        placeholder="Enter HMAC secret..."
                    />
                </div>
            )}

            <div className="flex items-center justify-between">
                <div>
                    <Label>Validate Payload Schema</Label>
                    <p className="text-xs text-muted-foreground">Reject invalid payloads</p>
                </div>
                <Switch
                    checked={(nodeData.validateSchema as boolean) || false}
                    onCheckedChange={(checked) => handleChange('validateSchema', checked)}
                />
            </div>

            <div className="p-3 bg-orange-50 rounded-lg border border-orange-200">
                <div className="text-xs text-orange-700">
                    <Link className="inline h-3 w-3 mr-1" />
                    This webhook will be available at the generated URL once the workflow is deployed.
                </div>
            </div>
        </div>
    );

    // ============ SUBWORKFLOW NODE ============
    const renderSubWorkflowConfig = () => (
        <div className="space-y-4">
            <div className="space-y-2">
                <Label>Node Label</Label>
                <Input
                    value={(nodeData.label as string) || ''}
                    onChange={(e) => handleChange('label', e.target.value)}
                    placeholder="Sub-Workflow"
                />
            </div>

            <div className="space-y-2">
                <Label>Select Workflow</Label>
                <WorkflowSelector
                    value={(nodeData.workflowId as string) || ''}
                    valueName={(nodeData.workflowName as string) || ''}
                    currentWorkflowId={workflowId}
                    onChange={(id, name) => {
                        // Update both values in single call to avoid stale data issues
                        if (nodeId) {
                            onUpdate(nodeId, { ...nodeData, workflowId: id, workflowName: name });
                        }
                    }}
                    placeholder="Select a workflow to execute..."
                />
                <p className="text-xs text-muted-foreground">
                    Choose the workflow to run as a sub-workflow
                </p>
            </div>

            <div className="space-y-2">
                <Label>Input Mappings</Label>
                <div className="space-y-2">
                    {Object.entries((nodeData.inputMappings as Record<string, string>) || {}).map(([key, value]) => (
                        <div key={key} className="flex items-center gap-2">
                            <Input
                                className="flex-1"
                                value={key}
                                placeholder="Sub-workflow input"
                                onChange={(e) => {
                                    const newMappings = { ...(nodeData.inputMappings as Record<string, string>) || {} };
                                    delete newMappings[key];
                                    newMappings[e.target.value] = value;
                                    handleChange('inputMappings', newMappings);
                                }}
                            />
                            <span className="text-muted-foreground">=</span>
                            <Input
                                className="flex-1"
                                value={value}
                                placeholder="{{variable}}"
                                onChange={(e) => {
                                    handleChange('inputMappings', {
                                        ...((nodeData.inputMappings as Record<string, string>) || {}),
                                        [key]: e.target.value
                                    });
                                }}
                            />
                            <Button
                                size="icon"
                                variant="ghost"
                                className="h-8 w-8"
                                onClick={() => {
                                    const newMappings = { ...(nodeData.inputMappings as Record<string, string>) || {} };
                                    delete newMappings[key];
                                    handleChange('inputMappings', newMappings);
                                }}
                            >
                                <Trash className="h-3 w-3 text-red-500" />
                            </Button>
                        </div>
                    ))}
                    <Button
                        size="sm"
                        variant="outline"
                        className="w-full"
                        onClick={() => {
                            handleChange('inputMappings', {
                                ...((nodeData.inputMappings as Record<string, string>) || {}),
                                '': ''
                            });
                        }}
                    >
                        <Plus className="h-3 w-3 mr-1" />
                        Add Input Mapping
                    </Button>
                </div>
            </div>

            <div className="space-y-2">
                <Label>Execution Mode</Label>
                <select
                    className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                    value={(nodeData.executionMode as string) || 'sync'}
                    onChange={(e) => handleChange('executionMode', e.target.value)}
                >
                    <option value="sync">Synchronous (Wait for completion)</option>
                    <option value="async">Asynchronous (Fire and forget)</option>
                </select>
            </div>

            <div className="space-y-2">
                <Label>Timeout (seconds)</Label>
                <Input
                    type="number"
                    min="0"
                    value={(nodeData.timeout as number) || 300}
                    onChange={(e) => handleChange('timeout', parseInt(e.target.value))}
                />
            </div>

            <div className="p-3 bg-indigo-50 rounded-lg border border-indigo-200">
                <div className="text-xs text-indigo-700">
                    <TreeStructure className="inline h-3 w-3 mr-1" />
                    Sub-workflows inherit the parent execution context and can access parent variables.
                </div>
            </div>
        </div>
    );

    // ============ HUMAN-IN-LOOP NODE ============
    const renderHumanInLoopConfig = () => {
        const options = (nodeData.options as Array<{ id: string; label: string }>) || [
            { id: 'approve', label: 'Approve' },
            { id: 'reject', label: 'Reject' }
        ];

        return (
            <div className="space-y-4">
                <div className="space-y-2">
                    <Label>Node Label</Label>
                    <Input
                        value={(nodeData.label as string) || ''}
                        onChange={(e) => handleChange('label', e.target.value)}
                        placeholder="Human Approval"
                    />
                </div>

                <div className="space-y-2">
                    <Label>Approval Title</Label>
                    <Input
                        value={(nodeData.title as string) || ''}
                        onChange={(e) => handleChange('title', e.target.value)}
                        placeholder="Approval Required"
                    />
                </div>

                <PromptEditor
                    label="Description"
                    value={(nodeData.description as string) || ''}
                    onChange={(v) => handleChange('description', v)}
                    placeholder="Describe what needs to be approved..."
                    nodes={nodes}
                    currentNodeId={nodeId}
                    minHeight="80px"
                />

                <div className="space-y-2">
                    <Label>Approver Type</Label>
                    <select
                        className="w-full h-9 rounded-md border px-3 text-sm bg-background"
                        value={(nodeData.approverType as string) || 'user'}
                        onChange={(e) => handleChange('approverType', e.target.value)}
                    >
                        <option value="user">Specific User</option>
                        <option value="role">Role-based</option>
                        <option value="group">User Group</option>
                        <option value="any">Any Authenticated User</option>
                    </select>
                </div>

                <div className="space-y-2">
                    <Label>Decision Options</Label>
                    <div className="space-y-2">
                        {options.map((option, i) => (
                            <div key={i} className="flex items-center gap-2">
                                <Input
                                    className="flex-1"
                                    value={option.label}
                                    placeholder="Option label"
                                    onChange={(e) => {
                                        const newOptions = [...options];
                                        newOptions[i] = {
                                            ...newOptions[i],
                                            label: e.target.value,
                                            id: e.target.value.toLowerCase().replace(/\s+/g, '_')
                                        };
                                        handleChange('options', newOptions);
                                    }}
                                />
                                <Button
                                    size="icon"
                                    variant="ghost"
                                    className="h-8 w-8"
                                    onClick={() => {
                                        handleChange('options', options.filter((_, idx) => idx !== i));
                                    }}
                                    disabled={options.length <= 2}
                                >
                                    <Trash className="h-3 w-3 text-red-500" />
                                </Button>
                            </div>
                        ))}
                        <Button
                            size="sm"
                            variant="outline"
                            className="w-full"
                            onClick={() => {
                                handleChange('options', [...options, { id: `option_${Date.now()}`, label: '' }]);
                            }}
                        >
                            <Plus className="h-3 w-3 mr-1" />
                            Add Option
                        </Button>
                    </div>
                </div>

                <div className="space-y-2">
                    <Label>SLA (Hours)</Label>
                    <Input
                        type="number"
                        min="0"
                        value={(nodeData.slaHours as number) || ''}
                        onChange={(e) => handleChange('slaHours', e.target.value ? parseInt(e.target.value) : null)}
                        placeholder="Optional timeout in hours"
                    />
                </div>

                <div className="flex items-center justify-between">
                    <div>
                        <Label>Require Comment</Label>
                        <p className="text-xs text-muted-foreground">Approver must provide reason</p>
                    </div>
                    <Switch
                        checked={(nodeData.requireComment as boolean) || false}
                        onCheckedChange={(checked) => handleChange('requireComment', checked)}
                    />
                </div>

                <div className="flex items-center justify-between">
                    <div>
                        <Label>Enable Escalation</Label>
                        <p className="text-xs text-muted-foreground">Auto-escalate if SLA exceeded</p>
                    </div>
                    <Switch
                        checked={(nodeData.escalationEnabled as boolean) || false}
                        onCheckedChange={(checked) => handleChange('escalationEnabled', checked)}
                    />
                </div>

                <div className="p-3 bg-red-50 rounded-lg border border-red-200">
                    <div className="text-xs text-red-700">
                        <UserCircle className="inline h-3 w-3 mr-1" />
                        Workflow will pause here until a human makes a decision. Each option creates a separate output branch.
                    </div>
                </div>
            </div>
        );
    };

    const renderConfig = () => {
        switch (nodeType) {
            case 'start':
                return renderStartConfig();
            case 'llm':
                return renderLLMConfig();
            case 'output':
                return renderOutputConfig();
            case 'knowledge':
                return renderKnowledgeConfig();
            case 'code':
                return renderCodeConfig();
            case 'http':
                return renderHttpConfig();
            case 'ifelse':
                return renderIfElseConfig();
            case 'iteration':
                return renderIterationConfig();
            case 'template':
                return renderTemplateConfig();
            case 'aggregator':
                return renderAggregatorConfig();
            case 'classifier':
                return renderClassifierConfig();
            case 'skill':
                return renderSkillNodeConfig();
            case 'connector':
                return renderConnectorNodeConfig();
            case 'agent':
                return renderAgentNodeConfig();
            case 'data':
                return renderDataNodeConfig();
            case 'analysis':
                return renderAnalysisNodeConfig();
            case 'compliance':
                return renderComplianceNodeConfig();
            case 'research':
                return renderResearchNodeConfig();
            // Phase 3: Essential workflow nodes
            case 'wait':
                return renderWaitConfig();
            case 'variable_assigner':
                return renderVariableAssignerConfig();
            case 'list_operator':
                return renderListOperatorConfig();
            case 'notes':
                return renderNotesConfig();
            case 'document_extractor':
                return renderDocumentExtractorConfig();
            case 'parameter_extractor':
                return renderParameterExtractorConfig();
            case 'ocr':
                return renderOCRConfig();
            case 'notification':
                return renderNotificationConfig();
            case 'webhook_trigger':
                return renderWebhookTriggerConfig();
            case 'subworkflow':
                return renderSubWorkflowConfig();
            case 'human_in_loop':
                return renderHumanInLoopConfig();
            default:
                return (
                    <div className="space-y-4">
                        <div className="space-y-2">
                            <Label>Node Label</Label>
                            <Input
                                value={(nodeData.label as string) || ''}
                                onChange={(e) => handleChange('label', e.target.value)}
                            />
                        </div>
                    </div>
                );
        }
    };

    return (
        <>
            <div className="flex flex-col h-full bg-background">
                {/* Header - matching left panel style */}
                <div className="flex items-center justify-between px-4 py-3 border-b">
                    <div className="flex items-center gap-2">
                        <Icon className="h-5 w-5 text-muted-foreground" weight="duotone" />
                        <span className="text-sm font-medium">{(nodeData.label as string) || nodeType}</span>
                    </div>
                    <Button
                        variant="ghost"
                        size="icon"
                        onClick={toggleConfigPanel}
                        className="h-7 w-7 rounded-full"
                        title="Collapse panel"
                    >
                        <CaretRight className="h-4 w-4" weight="bold" />
                    </Button>
                </div>
                <ScrollArea className="flex-1">
                    <div className="p-4">{renderConfig()}</div>
                </ScrollArea>
            </div>

            {/* Enable Connector Dialog */}
            {connectorToEnable && connectorDetail && !detailLoading && (
                <EnableConnectorDialog
                    connector={connectorDetail}
                    onClose={() => setConnectorToEnable(null)}
                    onEnable={handleEnableConnector}
                    isLoading={enableMutation.isPending}
                />
            )}
        </>
    );
}
