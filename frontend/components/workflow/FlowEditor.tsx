'use client';

import { useCallback, useRef, useState, useEffect, Suspense, useMemo } from 'react';
import dynamic from 'next/dynamic';
import {
    ReactFlow,
    Background,
    Controls,
    ControlButton,
    MiniMap,
    addEdge,
    useNodesState,
    useEdgesState,
    type Connection,
    type Node,
    type Edge,
    type ReactFlowInstance,
    BackgroundVariant,
} from '@xyflow/react';
// CSS is imported globally in app/(dashboard)/layout.tsx
import { GenerativeO } from '@/components/ui/GenerativeO';
import { MagicWand, Sliders, CaretLeft, CaretRight, CursorClick } from '@phosphor-icons/react';
import { useWorkflowBuilderLayout } from '@/lib/stores/workflow-builder-layout';
import { cn } from '@/lib/utils';
import { autoLayoutNodes } from '@/lib/workflow/auto-layout';
import { applyAIChanges } from '@/lib/workflow/apply-ai-changes';
import { AnimatedEdge } from './AnimatedEdge';
import { CanvasChatPanel } from './CanvasChatPanel';

import {
    StartNode,
    LLMNode,
    OutputNode,
    KnowledgeNode,
    CodeNode,
    HttpRequestNode,
    IfElseNode,
    IterationNode,
    TemplateNode,
    VariableAggregatorNode,
    QuestionClassifierNode,
    SkillNode,
    ConnectorNode,
    AgentNode,
    DataNode,
    AnalysisNode,
    ComplianceNode,
    ResearchNode,
    // Phase 3: Essential workflow nodes
    WaitNode,
    VariableAssignerNode,
    ListOperatorNode,
    NotesNode,
    DocumentExtractorNode,
    ParameterExtractorNode,
    OCRNode,
    NotificationNode,
    WebhookTriggerNode,
    SubWorkflowNode,
    HumanInLoopNode,
    // Phase 2: Workflow scheduling
    ScheduleTriggerNode,
    // Phase 3: Spreadsheet processing
    SpreadsheetNode,
} from './nodes';
// WorkflowToolbar removed - actions are now in the page header for consistency
// WorkflowNav removed - replaced by bottom panel in page.tsx
import { useWorkflowStore, useWorkflowActions, selectIsDirty, selectCurrentWorkflowId, selectPendingNodes, selectPendingEdges } from '@/lib/stores/workflow';
import { toast } from 'sonner';
import { type WorkflowNode as APINode, type WorkflowEdge as APIEdge, type WorkflowNodeType } from '@/lib/api/workflows';
import { API_BASE_URL } from '@/lib/api';
import { NodeContextMenu } from './NodeContextMenu';
import { useWorkflow, useUpdateWorkflow } from '@/hooks/useWorkflow';

// Lazy load heavy panels - only loaded when needed
const ConfigPanel = dynamic(() => import('./ConfigPanel').then(mod => ({ default: mod.ConfigPanel })), {
    loading: () => <div className="w-80 p-4 animate-pulse bg-muted rounded-lg" />,
    ssr: false,
});

const TestRunPanel = dynamic(() => import('./TestRunPanel').then(mod => ({ default: mod.TestRunPanel })), {
    loading: () => null,
    ssr: false,
});

// LogsPanel, MonitoringPanel, APIAccessPanel moved to BottomPanel (page.tsx)

// Import TestRunResult type from separate file to avoid circular dependency
import type { TestRunResult } from './types';

// Edge types for animated edges during execution
const EDGE_TYPES = {
    animated: AnimatedEdge,
    default: AnimatedEdge,
};

// Node types defined outside component for stable reference
// This object is created once at module load, not on every render
const NODE_TYPES = {
    start: StartNode,
    llm: LLMNode,
    output: OutputNode,
    knowledge: KnowledgeNode,
    code: CodeNode,
    http: HttpRequestNode,
    ifelse: IfElseNode,
    iteration: IterationNode,
    template: TemplateNode,
    aggregator: VariableAggregatorNode,
    classifier: QuestionClassifierNode,
    skill: SkillNode,
    connector: ConnectorNode,
    agent: AgentNode,
    data: DataNode,
    analysis: AnalysisNode,
    compliance: ComplianceNode,
    research: ResearchNode,
    // Phase 3: Essential workflow nodes
    wait: WaitNode,
    variable_assigner: VariableAssignerNode,
    list_operator: ListOperatorNode,
    notes: NotesNode,
    document_extractor: DocumentExtractorNode,
    parameter_extractor: ParameterExtractorNode,
    ocr: OCRNode,
    notification: NotificationNode,
    webhook_trigger: WebhookTriggerNode,
    subworkflow: SubWorkflowNode,
    human_in_loop: HumanInLoopNode,
    // Phase 2: Workflow scheduling
    schedule_trigger: ScheduleTriggerNode,
    // Phase 3: Spreadsheet processing
    spreadsheet: SpreadsheetNode,
};

const defaultNodeData: Record<string, Record<string, unknown>> = {
    start: { label: 'Start', variables: [] },
    llm: { label: 'LLM', model: '', systemPrompt: '', userPrompt: '', temperature: 0.7, maxTokens: 4096 },
    output: { label: 'End', outputVariables: [] },
    knowledge: { label: 'Knowledge Retrieval' },
    code: { label: 'Code', language: 'python3', code: '' },
    http: { label: 'HTTP Request', method: 'GET', url: '', headers: {}, body: '' },
    ifelse: { label: 'IF/ELSE', conditions: [], logicalOperator: 'and' },
    iteration: { label: 'Iteration' },
    template: { label: 'Template', template: '' },
    aggregator: { label: 'Variable Aggregator', inputVariables: [], aggregationType: 'concat' },
    classifier: { label: 'Question Classifier', classes: [] },
    skill: { label: 'Skill', skillName: '', skillCategory: 'web', inputs: {} },
    connector: { label: 'Connector', connectorId: '', connectorName: '', connectorType: 'rest', query: '', inputs: {} },
    agent: { label: 'Agent', agentId: '', agentName: '', userPrompt: '', skills: [], connectors: [] },
    data: { label: 'Data Retrieval' },
    analysis: { label: 'Analysis', analysisType: 'fundamental' },
    compliance: { label: 'Compliance' },
    'kyc-aml': { label: 'KYC/AML' },
    'market-abuse': { label: 'Market Abuse' },
    'report-generator': { label: 'Report Generator' },
    research: { label: 'Research' },
    // Phase 3: Essential workflow nodes
    wait: { label: 'Wait', waitType: 'duration', duration: 5, durationUnit: 'seconds' },
    variable_assigner: { label: 'Variable Assigner', assignments: [], outputMode: 'merge' },
    list_operator: { label: 'List Operator', inputArray: '', operation: 'first' },
    notes: { label: 'Note', content: '', color: 'yellow' },
    document_extractor: { label: 'Document Extractor', inputDocument: '', extractionType: 'full_text', outputFormat: 'text' },
    parameter_extractor: { label: 'Parameter Extractor', inputText: '', parameters: [], strictMode: false },
    ocr: { label: 'OCR', inputImage: '', provider: 'tesseract', language: 'eng', outputFormat: 'text', preprocessImage: false, detectTables: false, detectHandwriting: false },
    notification: { label: 'Notification', channel: 'email', recipients: '', subject: '', messageTemplate: '', priority: 'normal' },
    webhook_trigger: { label: 'Webhook Trigger', endpointPath: '', authType: 'hmac', responseMode: 'async', syncTimeoutSeconds: 30, expectedContentType: 'json', rateLimitPerMinute: 60, isActive: true },
    subworkflow: { label: 'Sub-Workflow', workflowId: '', inputMappings: [], outputVariable: 'result', async: false, timeout: 300, failOnError: true },
    human_in_loop: { label: 'Human Approval', title: '', description: '', approverType: 'user', approverConfig: {}, options: [{ id: 'approve', label: 'Approve' }, { id: 'reject', label: 'Reject' }], slaHours: 24, escalationEnabled: false, requireComment: false },
    // Phase 2: Workflow scheduling
    schedule_trigger: { label: 'Schedule Trigger', scheduleType: 'cron', cronExpression: '0 9 * * 1-5', intervalSeconds: 3600, scheduledTime: '', timezone: 'UTC', isEnabled: true, inputVariables: {}, maxExecutions: null, retryOnFailure: false, maxRetries: 3 },
    // Phase 3: Spreadsheet processing
    spreadsheet: { label: 'Spreadsheet', filePath: '', inputData: '', fileType: 'auto', sheetName: '', operations: [], outputFormat: 'json' },
};

// Initial workflow with Start node
const initialNodes: Node[] = [
    {
        id: 'start-1',
        type: 'start',
        position: { x: 100, y: 200 },
        data: { label: 'Start', variables: [] },
    },
];
const initialEdges: Edge[] = [];

interface FlowEditorProps {
    workflowId?: string;
    readOnly?: boolean;
}

// Configuration Panel Wrapper - handles collapsed state and empty state
interface ConfigPanelWrapperProps {
    selectedNode: Node | null;
    nodes: Node[];
    workflowId?: string;
    onClose: () => void;
    onUpdate: (nodeId: string, data: Record<string, unknown>) => void;
}

function ConfigPanelWrapper({ selectedNode, nodes, workflowId, onClose, onUpdate }: ConfigPanelWrapperProps) {
    const { isConfigCollapsed, toggleConfigPanel } = useWorkflowBuilderLayout();
    const [panelWidth, setPanelWidth] = useState(360);
    const [isResizing, setIsResizing] = useState(false);
    const minWidth = 300;
    const maxWidth = 500;

    // Handle resizing
    useEffect(() => {
        const handleMouseMove = (e: MouseEvent) => {
            if (!isResizing) return;
            // Calculate width from right edge of window
            const newWidth = window.innerWidth - e.clientX;
            setPanelWidth(Math.min(Math.max(newWidth, minWidth), maxWidth));
        };

        const handleMouseUp = () => {
            setIsResizing(false);
        };

        if (isResizing) {
            document.addEventListener('mousemove', handleMouseMove);
            document.addEventListener('mouseup', handleMouseUp);
        }

        return () => {
            document.removeEventListener('mousemove', handleMouseMove);
            document.removeEventListener('mouseup', handleMouseUp);
        };
    }, [isResizing]);

    // Collapsed state - full height rail with vertical text
    if (isConfigCollapsed) {
        return (
            <button
                onClick={toggleConfigPanel}
                className={cn(
                    'h-full w-10 flex flex-col items-center justify-center gap-3',
                    'bg-white border-l border-slate-200',
                    'hover:bg-slate-50 transition-all duration-200 ease-out',
                    'focus:outline-none focus:ring-2 focus:ring-inset focus:ring-primary/20',
                    'group'
                )}
                aria-label="Open configuration panel"
                title="Open configuration panel"
            >
                <Sliders size={20} className="text-slate-500 group-hover:text-slate-700 transition-colors" weight="duotone" />
                <span
                    className="text-xs font-medium text-slate-500 group-hover:text-slate-700 transition-colors"
                    style={{ writingMode: 'vertical-rl', textOrientation: 'mixed' }}
                >
                    Configure
                </span>
                <CaretLeft className="h-4 w-4 text-slate-400 group-hover:text-slate-600 transition-colors" weight="bold" />
            </button>
        );
    }

    // No node selected - show prompt
    if (!selectedNode) {
        return (
            <div
                className="h-full bg-white border-l border-slate-200 flex flex-col transition-all duration-300 ease-out"
                style={{ width: panelWidth }}
            >
                {/* Resize handle */}
                <div
                    className="absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-slate-200 transition-colors z-10"
                    onMouseDown={(e) => {
                        e.preventDefault();
                        setIsResizing(true);
                    }}
                />

                {/* Header */}
                <div className="flex items-center justify-between px-4 py-3 border-b">
                    <div className="flex items-center gap-2">
                        <Sliders className="h-5 w-5 text-slate-500" weight="duotone" />
                        <span className="text-sm font-medium">Configure</span>
                    </div>
                    <button
                        onClick={toggleConfigPanel}
                        className="h-7 w-7 rounded-full hover:bg-slate-100 flex items-center justify-center transition-colors"
                        title="Collapse panel"
                    >
                        <CaretRight className="h-4 w-4" weight="bold" />
                    </button>
                </div>

                {/* Empty state */}
                <div className="flex-1 flex flex-col items-center justify-center p-6 text-center">
                    <div className="w-16 h-16 rounded-full bg-slate-100 flex items-center justify-center mb-4">
                        <CursorClick className="h-8 w-8 text-slate-400" weight="duotone" />
                    </div>
                    <h3 className="text-sm font-medium text-slate-700 mb-2">Select a node</h3>
                    <p className="text-xs text-slate-500 max-w-[200px]">
                        Click on any node in the canvas to view and edit its configuration
                    </p>
                </div>
            </div>
        );
    }

    // Node selected - show full ConfigPanel
    return (
        <div
            className="relative h-full bg-white border-l border-slate-200 transition-all duration-300 ease-out"
            style={{ width: panelWidth }}
        >
            {/* Resize handle */}
            <div
                className="absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-slate-200 transition-colors z-10"
                onMouseDown={(e) => {
                    e.preventDefault();
                    setIsResizing(true);
                }}
            />
            <ConfigPanel
                node={selectedNode}
                nodes={nodes}
                workflowId={workflowId}
                onClose={onClose}
                onUpdate={onUpdate}
            />
        </div>
    );
}

export function FlowEditor({ workflowId, readOnly = false }: FlowEditorProps) {
    const reactFlowWrapper = useRef<HTMLDivElement>(null);
    const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance | null>(null);

    // API Hooks - only fetch if we have a valid workflowId
    const { data: workflowData, isLoading: isWorkflowLoading, isFetching } = useWorkflow(workflowId || '');
    const updateWorkflowMutation = useUpdateWorkflow();

    // Only show loading if we're actually fetching and don't have data yet
    const showLoading = workflowId && isWorkflowLoading && !workflowData;

    // Context Menu State
    const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);

    const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
    const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

    // AI preview: pending nodes/edges from Zustand store (shown as ghost nodes)
    const pendingNodes = useWorkflowStore(selectPendingNodes);
    const pendingEdges = useWorkflowStore(selectPendingEdges);

    // Merge real + pending nodes for display (pending nodes get ghost styling)
    const displayNodes = useMemo(() => {
        if (pendingNodes.length === 0) return nodes;
        const existingIds = new Set(nodes.map((n) => n.id));
        const ghostNodes = pendingNodes
            .filter((n) => !existingIds.has(n.id))
            .map((n) => ({
                ...n,
                data: { ...n.data, _isPending: true },
                style: { opacity: 0.55, filter: 'saturate(0.6)' },
                draggable: false,
                selectable: false,
            }));
        return [...nodes, ...ghostNodes];
    }, [nodes, pendingNodes]);

    const displayEdges = useMemo(() => {
        if (pendingEdges.length === 0) return edges;
        const existingIds = new Set(edges.map((e) => e.id));
        const ghostEdges = pendingEdges
            .filter((e) => !existingIds.has(e.id))
            .map((e) => ({
                ...e,
                style: { opacity: 0.4, strokeDasharray: '6 3' },
                animated: true,
            }));
        return [...edges, ...ghostEdges];
    }, [edges, pendingEdges]);

    // Auto fit-view when pending AI preview nodes appear
    const prevPendingCountRef = useRef(0);
    useEffect(() => {
        if (pendingNodes.length > 0 && prevPendingCountRef.current === 0 && reactFlowInstance) {
            // New preview nodes appeared — fit view after a tick so React Flow lays them out
            setTimeout(() => {
                reactFlowInstance.fitView({ padding: 0.3, maxZoom: 1, duration: 300 });
            }, 50);
        }
        prevPendingCountRef.current = pendingNodes.length;
    }, [pendingNodes.length, reactFlowInstance]);

    // Get current Zustand state for initialization check
    const currentWorkflowId = useWorkflowStore(selectCurrentWorkflowId);
    const isDirty = useWorkflowStore(selectIsDirty);
    const { initializeForWorkflow, syncNodes, syncEdges } = useWorkflowActions();

    // Track initialization to prevent double-init
    const hasInitializedRef = useRef<string | null>(null);

    // Initialize from API data or restore from Zustand
    // IMPORTANT: Don't include Zustand arrays in deps to avoid infinite loops
    useEffect(() => {
        // If we don't have a workflowId yet, nothing to do
        if (!workflowId) return;

        // Case 1: Already initialized for this workflow, skip
        if (hasInitializedRef.current === workflowId) {
            return;
        }

        // Case 2: Store already has valid data for THIS workflow
        // Restore from Zustand (preserves unsaved changes from Steps view)
        if (currentWorkflowId === workflowId) {
            const storeState = useWorkflowStore.getState();
            if (storeState.nodes.length > 0 || storeState.isDirty) {
                // @ts-ignore - React Flow types mismatch slightly with API types but are compatible
                setNodes(storeState.nodes);
                // @ts-ignore
                setEdges(storeState.edges);
                hasInitializedRef.current = workflowId;

                // Trigger fitView after nodes are loaded
                if (reactFlowInstance && storeState.nodes.length > 0) {
                    setTimeout(() => {
                        reactFlowInstance.fitView({
                            padding: 0.3,
                            maxZoom: 1,
                            minZoom: 0.5,
                        });
                    }, 100);
                }
                return;
            }
        }

        // Case 3: Wait for API data to be ready
        if (!workflowData?.spec) return;

        // Case 4: Use API data (first load or different workflow)
        const newNodes = workflowData.spec.nodes.length > 0 ? workflowData.spec.nodes : initialNodes;
        const newEdges = workflowData.spec.edges;

        // Atomic initialization - sets workflowId + nodes + edges together
        // @ts-ignore - React Flow types mismatch slightly with API types but are compatible
        initializeForWorkflow(workflowId, newNodes, newEdges);
        hasInitializedRef.current = workflowId;

        // Also update local React Flow state
        // @ts-ignore
        setNodes(newNodes);
        // @ts-ignore
        setEdges(newEdges);

        // Trigger fitView after nodes are loaded
        if (reactFlowInstance && newNodes.length > 0) {
            setTimeout(() => {
                reactFlowInstance.fitView({
                    padding: 0.3,
                    maxZoom: 1,
                    minZoom: 0.5,
                });
            }, 100);
        }
    }, [workflowId, workflowData, currentWorkflowId, isDirty, setNodes, setEdges, initializeForWorkflow, reactFlowInstance]);

    // Hydrate execution results from latest completed execution on page load
    const lastExecutionId = useWorkflowStore((s) => s.lastExecutionId);
    useEffect(() => {
        if (!workflowId || !currentWorkflowId || currentWorkflowId !== workflowId) return;
        // Don't re-hydrate if we already have results from this session
        const storeState = useWorkflowStore.getState();
        if (Object.keys(storeState.executionResults).length > 0) return;

        let cancelled = false;
        (async () => {
            try {
                const { fetchLatestExecution } = await import('@/lib/api/workflow-logs');
                const latest = await fetchLatestExecution(workflowId);
                if (cancelled || !latest?.execution_trace?.length) return;
                // Don't overwrite if execution started while we were fetching
                if (useWorkflowStore.getState().isExecuting) return;
                // Prevent duplicate hydration
                if (useWorkflowStore.getState().lastExecutionId === latest.id) return;

                const results: Record<string, { nodeId: string; status: 'pending' | 'running' | 'complete' | 'error'; result?: unknown; error?: string }> = {};
                const statuses: Record<string, 'pending' | 'running' | 'complete' | 'error'> = {};
                for (const step of latest.execution_trace) {
                    const status = step.status === 'completed' ? 'complete' as const
                        : step.status === 'failed' ? 'error' as const
                        : 'pending' as const;
                    results[step.node_id] = {
                        nodeId: step.node_id,
                        status,
                        result: step.output,
                        error: step.error || undefined,
                    };
                    statuses[step.node_id] = status;
                }
                useWorkflowStore.setState({
                    executionResults: results,
                    nodeStatusMap: statuses,
                    lastExecutionId: latest.id,
                });
            } catch {
                // Non-critical — fresh page just won't show previous results
            }
        })();
        return () => { cancelled = true; };
    }, [workflowId, currentWorkflowId]); // eslint-disable-line react-hooks/exhaustive-deps

    const {
        isExecuting,
        setIsExecuting,
        setSelectedNodeId,
        selectedNodeId,
        setActiveNodeId,
        setNodeStatus,
        setExecutionResult,
        resetExecution,
        testRunOpen,
        setTestRunOpen,
    } = useWorkflowStore();

    // Sync local React Flow state to Zustand store for Steps view
    // This enables Canvas <-> Steps synchronization
    // IMPORTANT: Uses syncNodes/syncEdges which don't mark dirty
    // User changes go through setNodes/setEdges which do mark dirty
    useEffect(() => {
        // Only sync if store is initialized for this workflow
        if (currentWorkflowId === workflowId && workflowId) {
            syncNodes(nodes);
        }
    }, [nodes, syncNodes, currentWorkflowId, workflowId]);

    useEffect(() => {
        // Only sync if store is initialized for this workflow
        if (currentWorkflowId === workflowId && workflowId) {
            syncEdges(edges);
        }
    }, [edges, syncEdges, currentWorkflowId, workflowId]);

    const selectedNode = nodes.find((n) => n.id === selectedNodeId) || null;

    const onConnect = useCallback(
        (connection: Connection) => {
            // Add the new edge with animated type
            setEdges((eds) => {
                const newEdges = addEdge({
                    ...connection,
                    type: 'animated', // Use our custom animated edge
                }, eds);
                return newEdges;
            });

            // Immediately auto-layout nodes after connection for clean arrangement
            setTimeout(() => {
                setNodes((currentNodes) => {
                    // Get current edges including the new one
                    const currentEdges = useWorkflowStore.getState().edges;
                    const layoutedNodes = autoLayoutNodes(currentNodes, currentEdges, {
                        direction: 'LR',
                        nodeWidth: 280,
                        nodeHeight: 120,
                        horizontalSpacing: 120,
                        verticalSpacing: 80,
                    });
                    return layoutedNodes;
                });

                // Fit view after layout
                setTimeout(() => {
                    reactFlowInstance?.fitView({
                        padding: 0.3,
                        maxZoom: 1,
                        minZoom: 0.5,
                        duration: 300,
                    });
                }, 50);
            }, 10);
        },
        [setEdges, setNodes, reactFlowInstance]
    );

    const onNodeClick = useCallback(
        (_: React.MouseEvent, node: Node) => {
            setSelectedNodeId(node.id);
            setContextMenu(null); // Close menu on node click
        },
        [setSelectedNodeId]
    );

    const onPaneClick = useCallback(() => {
        setSelectedNodeId(null);
        setContextMenu(null); // Close menu on pane click
    }, [setSelectedNodeId]);

    const onPaneContextMenu = useCallback(
        (event: MouseEvent | React.MouseEvent) => {
            event.preventDefault();
            setContextMenu({ x: event.clientX, y: event.clientY });
        },
        []
    );

    const handleAddNode = useCallback(
        (type: string) => {
            if (!reactFlowInstance || !contextMenu) return;

            const position = reactFlowInstance.screenToFlowPosition({
                x: contextMenu.x,
                y: contextMenu.y,
            });

            const newNode: Node = {
                id: `${type}-${Date.now()}`,
                type,
                position,
                data: defaultNodeData[type] || { label: type },
            };

            setNodes((nds) => [...nds, newNode]);
            setContextMenu(null);
        },
        [reactFlowInstance, contextMenu, setNodes]
    );

    const onDragOver = useCallback((event: React.DragEvent) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
    }, []);

    const onDrop = useCallback(
        (event: React.DragEvent) => {
            event.preventDefault();

            const type = event.dataTransfer.getData('application/reactflow');
            if (!type || !reactFlowInstance) return;

            const position = reactFlowInstance.screenToFlowPosition({
                x: event.clientX,
                y: event.clientY,
            });

            const newNode: Node = {
                id: `${type}-${Date.now()}`,
                type,
                position,
                data: defaultNodeData[type] || { label: type },
            };

            setNodes((nds) => [...nds, newNode]);
        },
        [reactFlowInstance, setNodes]
    );

    const handleNodeUpdate = useCallback(
        (nodeId: string, data: Record<string, unknown>) => {
            setNodes((nds) => nds.map((n) => (n.id === nodeId ? { ...n, data } : n)));
        },
        [setNodes]
    );

    // Auto-layout nodes in a clean left-to-right arrangement
    const handleTidyLayout = useCallback(() => {
        const layoutedNodes = autoLayoutNodes(nodes, edges, {
            direction: 'LR',
            nodeWidth: 280,
            nodeHeight: 120,
            horizontalSpacing: 120,
            verticalSpacing: 80,
        });
        setNodes(layoutedNodes);

        // Fit view after layout with a small delay for animation
        setTimeout(() => {
            reactFlowInstance?.fitView({
                padding: 0.3,
                maxZoom: 1,
                minZoom: 0.5,
                duration: 300,
            });
        }, 50);

        toast('Layout tidied', { description: 'Nodes have been arranged left-to-right.' });
    }, [nodes, edges, setNodes, reactFlowInstance]);

    const handleSave = async () => {
        if (!workflowId) {
            toast.error('Error', { description: 'Cannot save: No workflow ID found.' });
            return;
        }

        try {
            // Convert React Flow nodes to API format
            const apiNodes: APINode[] = nodes.map((node) => ({
                id: node.id,
                type: (node.type as WorkflowNodeType) || 'start',
                data: node.data as Record<string, unknown>,
                position: node.position,
            }));

            const apiEdges = edges.map((edge) => ({
                id: edge.id,
                source: edge.source,
                target: edge.target,
                sourceHandle: edge.sourceHandle || undefined,
                targetHandle: edge.targetHandle || undefined,
            }));

            await updateWorkflowMutation.mutateAsync({
                id: workflowId,
                spec: { nodes: apiNodes, edges: apiEdges }
            });
            toast('Workflow saved', { description: 'Your workflow has been saved successfully.' });
        } catch (error) {
            console.error('[FlowEditor] Save failed:', error);
            const message = error instanceof Error ? error.message : 'Could not save workflow.';
            toast.error('Save failed', { description: message });
        }
    };

    // Real backend test run handler
    const handleTestRun = async (inputs: Record<string, string>): Promise<TestRunResult> => {
        try {
            resetExecution();
            setIsExecuting(true);

            // Convert React Flow nodes/edges to API format
            const apiNodes: APINode[] = nodes.map((node) => ({
                id: node.id,
                type: (node.type as WorkflowNodeType) || 'start',
                data: node.data as Record<string, unknown>,
                position: node.position,
            }));

            const apiEdges: APIEdge[] = edges.map((edge) => ({
                id: edge.id,
                source: edge.source,
                target: edge.target,
                sourceHandle: edge.sourceHandle || undefined,
                targetHandle: edge.targetHandle || undefined,
            }));

            // Use streaming endpoint for real-time tracing
            const executeUrl = `${API_BASE_URL}/api/v1/workflows/execute-stream`;

            // Only send workflow_id if it's a valid UUID (not a local workflow)
            const isValidWorkflowId = workflowId && !workflowId.startsWith('local-');

            // Read CSRF token from cookie (required for cookie-based auth POST requests)
            const csrfMatch = document.cookie.match(/(?:^|; )csrf_token=([^;]*)/);
            const csrfToken = csrfMatch ? decodeURIComponent(csrfMatch[1]) : undefined;

            const response = await fetch(executeUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
                },
                credentials: 'include',
                body: JSON.stringify({
                    nodes: apiNodes,
                    edges: apiEdges,
                    inputs,
                    workflow_id: isValidWorkflowId ? workflowId : null
                })
            });

            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

            const reader = response.body?.getReader();
            const decoder = new TextDecoder();
            let finalResult: TestRunResult | null = null;

            // Collect node results as they stream in for the TRACING tab
            const collectedNodeResults: Array<{
                nodeId: string;
                nodeName: string;
                status: 'success' | 'failed';
                output?: unknown;
                elapsedMs: number;
            }> = [];
            let totalElapsedMs = 0;

            if (reader) {
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    const chunk = decoder.decode(value);
                    const lines = chunk.split('\n');

                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            try {
                                const event = JSON.parse(line.substring(6));

                                // Batch state updates to reduce re-renders
                                switch (event.type) {
                                    case 'node_started':
                                        // Single batched update for node start
                                        useWorkflowStore.setState((state) => ({
                                            activeNodeId: event.node_id,
                                            nodeStatusMap: { ...state.nodeStatusMap, [event.node_id]: 'running' },
                                        }));
                                        break;
                                    case 'node_complete': {
                                        // Debug logging
                                        console.log('[SSE] node_complete event:', JSON.stringify(event, null, 2));
                                        console.log('[SSE] event.result.output:', event.result?.output);

                                        // Single batched update for node complete
                                        useWorkflowStore.setState((state) => ({
                                            executionResults: {
                                                ...state.executionResults,
                                                [event.node_id]: {
                                                    nodeId: event.node_id,
                                                    status: event.result.status === 'success' ? 'complete' : 'error',
                                                    result: event.result.output,
                                                    error: event.result.error,
                                                },
                                            },
                                            nodeStatusMap: {
                                                ...state.nodeStatusMap,
                                                [event.node_id]: event.result.status === 'success' ? 'complete' : 'error',
                                            },
                                        }));

                                        // Collect node result for tracing
                                        const nodeElapsed = event.result.elapsed_ms || 0;
                                        totalElapsedMs += nodeElapsed;

                                        // Find the node to get its label
                                        const nodeForLabel = nodes.find(n => n.id === event.node_id);
                                        const nodeName = (nodeForLabel?.data as Record<string, unknown>)?.label as string
                                            || event.node_id;

                                        collectedNodeResults.push({
                                            nodeId: event.node_id,
                                            nodeName,
                                            status: event.result.status === 'success' ? 'success' : 'failed',
                                            output: event.result.output,
                                            elapsedMs: nodeElapsed,
                                        });
                                        console.log('[SSE] collectedNodeResults after push:', JSON.stringify(collectedNodeResults, null, 2));
                                        break;
                                    }
                                    case 'complete': {
                                        // Extract final output from the results
                                        // Look for output from LLM nodes or Output nodes
                                        let finalOutput: unknown = "Workflow execution completed.";
                                        const results = event.results || {};

                                        // Find the last LLM or Output node result with text content
                                        for (const nodeResult of collectedNodeResults.slice().reverse()) {
                                            const output = nodeResult.output;
                                            if (output && typeof output === 'object') {
                                                const outputObj = output as Record<string, unknown>;
                                                // Check for text/message from LLM nodes
                                                if (outputObj.text || outputObj.message) {
                                                    finalOutput = outputObj.text || outputObj.message;
                                                    break;
                                                }
                                                // Check for content from Output nodes
                                                if (outputObj.content) {
                                                    finalOutput = outputObj.content;
                                                    break;
                                                }
                                            }
                                        }

                                        finalResult = {
                                            status: 'success',
                                            output: finalOutput,
                                            elapsedMs: totalElapsedMs,
                                            totalTokens: 0,
                                            nodeResults: collectedNodeResults,
                                        };
                                        break;
                                    }
                                    case 'error':
                                        useWorkflowStore.setState({
                                            isExecuting: false,
                                            activeNodeId: null,
                                        });
                                        return {
                                            status: 'failed',
                                            error: event.error,
                                            elapsedMs: totalElapsedMs,
                                            totalTokens: 0,
                                            nodeResults: collectedNodeResults
                                        };
                                }
                            } catch {
                                // Ignore malformed JSON lines
                            }
                        }
                    }
                }
            }

            useWorkflowStore.setState({
                isExecuting: false,
                activeNodeId: null,
            });

            // If we collected results but didn't get a 'complete' event, build result from collected data
            if (!finalResult && collectedNodeResults.length > 0) {
                let finalOutput: unknown = "Workflow execution completed.";
                // Find the last node result with text content
                for (const nodeResult of collectedNodeResults.slice().reverse()) {
                    const output = nodeResult.output;
                    if (output && typeof output === 'object') {
                        const outputObj = output as Record<string, unknown>;
                        if (outputObj.text || outputObj.message) {
                            finalOutput = outputObj.text || outputObj.message;
                            break;
                        }
                        if (outputObj.content) {
                            finalOutput = outputObj.content;
                            break;
                        }
                    }
                }
                finalResult = {
                    status: 'success',
                    output: finalOutput,
                    elapsedMs: totalElapsedMs,
                    totalTokens: 0,
                    nodeResults: collectedNodeResults,
                };
            }

            return finalResult || {
                status: 'success',
                output: "Done",
                elapsedMs: totalElapsedMs,
                totalTokens: 0,
                nodeResults: collectedNodeResults
            };

        } catch (error) {
            useWorkflowStore.setState({
                isExecuting: false,
                activeNodeId: null,
            });
            return {
                status: 'failed' as const,
                error: error instanceof Error ? error.message : 'Unknown error',
                elapsedMs: 0,
                totalTokens: 0,
                nodeResults: [],
            };
        }
    };

    if (showLoading) {
        return (
            <div className="flex h-full items-center justify-center">
                <div className="flex flex-col items-center gap-3">
                    <GenerativeO size={40} speed={1} animating={true} />
                    <p className="text-sm text-muted-foreground">Loading workflow...</p>
                </div>
            </div>
        );
    }

    // React Flow requires its parent container to have explicit dimensions.
    // Using flexbox with flex-1 and min-h-0 is the cleanest pattern.
    // Layout: [Chat Panel (left)] [Canvas (center)] [Config Panel (right)]

    return (
        <div className="flex h-full w-full overflow-hidden min-h-0">
            {/* AI Chat Panel - Left side panel */}
            <CanvasChatPanel
                workflowId={workflowId || ''}
                nodes={nodes}
                edges={edges}
                onNodesGenerated={(newNodes, newEdges) => {
                    // Save undo snapshot before applying
                    useWorkflowStore.getState().setUndoSnapshot({ nodes: [...nodes], edges: [...edges] });

                    // Extract deletions from proposed nodes with _action: 'delete'
                    const deletions = newNodes
                        .filter(n => (n.data as Record<string, unknown>)?._action === 'delete')
                        .map(n => ({ nodeId: n.id }));

                    // Filter out delete-only nodes from proposed nodes
                    const nonDeleteNodes = newNodes.filter(
                        n => (n.data as Record<string, unknown>)?._action !== 'delete'
                    );

                    const result = applyAIChanges(
                        nodes, edges, nonDeleteNodes, newEdges,
                        deletions.length > 0 ? { deletions } : undefined
                    );
                    setNodes(result.nodes);
                    setEdges(result.edges);

                    // Auto-layout after applying changes
                    setTimeout(() => {
                        setNodes((currentNodes) => {
                            const currentEdges = useWorkflowStore.getState().edges;
                            return autoLayoutNodes(currentNodes, currentEdges, {
                                direction: 'LR',
                                nodeWidth: 280,
                                nodeHeight: 120,
                                horizontalSpacing: 120,
                                verticalSpacing: 80,
                            });
                        });

                        // Fit view after layout
                        setTimeout(() => {
                            reactFlowInstance?.fitView({
                                padding: 0.3,
                                maxZoom: 1,
                                minZoom: 0.5,
                                duration: 300,
                            });
                        }, 50);
                    }, 10);
                }}
                onNodeSelect={(nodeId) => setSelectedNodeId(nodeId)}
                isReadOnly={readOnly}
            />

            {/* Canvas - Center area, takes remaining space */}
            <div
                className="flex-1 flex flex-col h-full min-h-0"
                ref={reactFlowWrapper}
            >
                {/* React Flow container - full height since toolbar is now in page header */}
                <div className="flex-1 relative" style={{ height: '100%' }}>
                    <ReactFlow
                        nodes={displayNodes}
                        edges={displayEdges}
                        onNodesChange={readOnly ? undefined : onNodesChange}
                        onEdgesChange={readOnly ? undefined : onEdgesChange}
                        onConnect={readOnly ? undefined : onConnect}
                        onNodeClick={onNodeClick}
                        onPaneClick={onPaneClick}
                        onPaneContextMenu={readOnly ? undefined : onPaneContextMenu}
                        onInit={setReactFlowInstance}
                        onDragOver={readOnly ? undefined : onDragOver}
                        onDrop={readOnly ? undefined : onDrop}
                        nodeTypes={NODE_TYPES}
                        edgeTypes={EDGE_TYPES}
                        nodesDraggable={!readOnly}
                        nodesConnectable={!readOnly}
                        elementsSelectable={!readOnly}
                        fitView
                        fitViewOptions={{
                            padding: 0.4,
                            maxZoom: 0.9,
                            minZoom: 0.4,
                            includeHiddenNodes: false,
                        }}
                        snapToGrid
                        snapGrid={[16, 16]}
                        minZoom={0.2}
                        maxZoom={1.5}
                        defaultEdgeOptions={{
                            type: 'animated', // Use our custom animated edge component
                        }}
                        proOptions={{ hideAttribution: false }}
                        className="!bg-slate-50"
                        style={{ width: '100%', height: '100%', position: 'absolute', top: 0, left: 0 }}
                        // Disable scroll zoom to prevent accidental zoom when hovering edges
                        zoomOnScroll={false}
                        zoomOnPinch={true}
                        panOnScroll={true}
                        // Larger connection radius for easier connecting
                        connectionRadius={40}
                        // Performance optimizations
                        onlyRenderVisibleElements
                    >
                        <Background
                            variant={BackgroundVariant.Dots}
                            gap={16}      // Tighter grid for cleaner dotted pattern
                            size={1}      // Small dots
                            color="#cbd5e1"  // More visible gray (slate-300)
                        />
                        <Controls
                            className="!bg-white/90 !backdrop-blur-sm !border-0 !shadow-md !rounded-xl !p-1 [&_button]:!border-0 [&_button]:!bg-transparent [&_button:hover]:!bg-slate-100"
                            showInteractive={false}  // Cleaner look
                        >
                            {!readOnly && (
                                <ControlButton
                                    onClick={handleTidyLayout}
                                    title="Tidy layout (arrange nodes left-to-right)"
                                    className="!bg-transparent hover:!bg-slate-100"
                                >
                                    <MagicWand className="h-4 w-4" />
                                </ControlButton>
                            )}
                        </Controls>
                        <MiniMap
                            nodeColor={(node) => {
                                const colors: Record<string, string> = {
                                    start: '#6b7280',
                                    llm: '#9333ea',
                                    output: '#0d9488',
                                    knowledge: '#16a34a',
                                    code: '#2563eb',
                                    http: '#ea580c',
                                    ifelse: '#ca8a04',
                                    iteration: '#4f46e5',
                                    template: '#db2777',
                                    aggregator: '#0891b2',
                                    classifier: '#7c3aed',
                                    skill: '#059669',
                                    connector: '#d97706',
                                    agent: '#7c3aed',
                                    data: '#0ea5e9',
                                    analysis: '#8b5cf6',
                                    compliance: '#dc2626',
                                    research: '#10b981',
                                    // Phase 3: Essential workflow nodes
                                    wait: '#4f46e5',
                                    variable_assigner: '#0891b2',
                                    list_operator: '#0891b2',
                                    notes: '#eab308',
                                    document_extractor: '#16a34a',
                                    parameter_extractor: '#7c3aed',
                                    ocr: '#0ea5e9',
                                    notification: '#ea580c',
                                    webhook_trigger: '#ea580c',
                                    subworkflow: '#4f46e5',
                                    human_in_loop: '#dc2626',
                                };
                                return colors[node.type || ''] || '#6b7280';
                            }}
                            maskColor="rgba(0, 0, 0, 0.1)"
                            className="!bg-white !border !border-slate-200 !shadow-md !rounded-lg"
                            pannable
                            zoomable
                        />
                    </ReactFlow>
                </div>

                {contextMenu && (
                    <NodeContextMenu
                        x={contextMenu.x}
                        y={contextMenu.y}
                        onAddNode={handleAddNode}
                        onClose={() => setContextMenu(null)}
                    />
                )}
            </div>

            {/* Configuration Panel - Right side */}
            {!testRunOpen && (
                <ConfigPanelWrapper
                    selectedNode={selectedNode}
                    nodes={nodes}
                    workflowId={workflowId}
                    onClose={() => setSelectedNodeId(null)}
                    onUpdate={handleNodeUpdate}
                />
            )}

            {testRunOpen && (
                <TestRunPanel
                    isOpen={testRunOpen}
                    onClose={() => setTestRunOpen(false)}
                    nodes={nodes}
                    onRun={handleTestRun}
                />
            )}
        </div>
    );
}