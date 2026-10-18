import type { RunStatus } from '@/lib/run/status';
import { create } from 'zustand';
import { useShallow } from 'zustand/react/shallow';
import type { Node, Edge } from '@xyflow/react';

// ============ Comparison Helpers ============
// Volatile fields set by React Flow on every drag/select — not part of workflow data.
const VOLATILE_NODE_KEYS = new Set([
    'position', 'selected', 'dragging', 'measured', 'width', 'height',
    'positionAbsolute', 'draggable', 'selectable', 'resizing',
]);

/**
 * Strip volatile fields from a node for dirty comparison.
 * Uses a replacer function so we allocate only the final string, not
 * intermediate cloned objects.
 */
const stableNodeReplacer = (key: string, value: unknown) =>
    VOLATILE_NODE_KEYS.has(key) ? undefined : value;

// Deep comparison for nodes — ignores position/selection/drag state
const nodesEqual = (a: Node[], b: Node[]): boolean => {
    if (a.length !== b.length) return false;
    return JSON.stringify(a, stableNodeReplacer) === JSON.stringify(b, stableNodeReplacer);
};

const edgesEqual = (a: Edge[], b: Edge[]): boolean => {
    if (a.length !== b.length) return false;
    return JSON.stringify(a) === JSON.stringify(b);
};

// ============ Types ============
interface ExecutionResult {
    nodeId: string;
    status: RunStatus;
    result?: unknown;
    error?: string;
}

type NodeStatus = RunStatus;

interface WorkflowState {
    // State
    nodes: Node[];
    edges: Edge[];
    selectedNodeId: string | null;
    activeNodeId: string | null;
    isExecuting: boolean;
    executionResults: Record<string, ExecutionResult>;
    nodeStatusMap: Record<string, NodeStatus>;
    testRunOpen: boolean;

    // Workflow identity tracking - prevents cross-workflow data pollution
    currentWorkflowId: string | null;

    // Last execution ID - prevents stale restoration
    lastExecutionId: string | null;

    // Unsaved changes tracking
    isDirty: boolean;
    lastSavedNodes: Node[];
    lastSavedEdges: Edge[];

    // Version viewing state
    viewingVersionId: string | null;       // null = viewing draft
    viewingVersionNumber: number | null;   // For display
    isViewingHistorical: boolean;          // true = read-only mode
    activeVersionId: string | null;        // The published/active version ID

    // AI Workflow Builder - Pending State
    pendingNodes: Node[];
    pendingEdges: Edge[];
    hasPendingChanges: boolean;
    isAIGenerating: boolean;

    // AI Undo Snapshot (single-level)
    undoSnapshot: { nodes: Node[]; edges: Edge[] } | null;

    // Canvas Chat Panel State
    isCanvasChatOpen: boolean;
}

interface WorkflowActions {
    // Actions
    setNodes: (nodes: Node[]) => void;
    setEdges: (edges: Edge[]) => void;
    addNode: (node: Node) => void;
    updateNodeData: (nodeId: string, data: Partial<Record<string, unknown>>) => void;
    removeNode: (nodeId: string) => void;
    setSelectedNodeId: (nodeId: string | null) => void;
    setActiveNodeId: (nodeId: string | null) => void;
    setNodeStatus: (nodeId: string, status: NodeStatus) => void;
    setIsExecuting: (executing: boolean) => void;
    setExecutionResult: (nodeId: string, result: ExecutionResult) => void;
    setTestRunOpen: (open: boolean) => void;
    resetExecution: () => void;
    reset: () => void;

    // Workflow identity actions
    initializeForWorkflow: (workflowId: string, nodes: Node[], edges: Edge[]) => void;
    isDataForWorkflow: (workflowId: string) => boolean;

    // Unsaved changes tracking actions
    markDirty: () => void;
    markClean: () => void;
    setLastSaved: (nodes: Node[], edges: Edge[]) => void;

    // Version viewing actions
    setViewingVersion: (versionId: string | null, versionNumber: number | null, isHistorical: boolean) => void;
    setActiveVersionId: (versionId: string | null) => void;
    switchToDraft: () => void;

    // Sync actions - don't mark dirty (for FlowEditor sync)
    syncNodes: (nodes: Node[]) => void;
    syncEdges: (edges: Edge[]) => void;

    // AI Workflow Builder - Pending State Actions
    setPending: (nodes: Node[], edges: Edge[]) => void;
    applyPending: () => void;
    discardPending: () => void;
    lockForAI: () => void;
    unlockFromAI: () => void;

    // AI Undo Actions
    setUndoSnapshot: (snapshot: { nodes: Node[]; edges: Edge[] } | null) => void;
    undoLastAIChange: () => void;

    // Canvas Chat Panel Actions
    toggleCanvasChat: () => void;
    setCanvasChatOpen: (open: boolean) => void;
}

export type WorkflowStore = WorkflowState & WorkflowActions;

// ============ Initial State ============
const initialState: WorkflowState = {
    nodes: [],
    edges: [],
    selectedNodeId: null,
    activeNodeId: null,
    isExecuting: false,
    executionResults: {},
    nodeStatusMap: {},
    testRunOpen: false,

    // Workflow identity tracking
    currentWorkflowId: null,
    lastExecutionId: null,

    // Unsaved changes tracking
    isDirty: false,
    lastSavedNodes: [],
    lastSavedEdges: [],

    // Version viewing state
    viewingVersionId: null,
    viewingVersionNumber: null,
    isViewingHistorical: false,
    activeVersionId: null,

    // AI Workflow Builder - Pending State
    pendingNodes: [],
    pendingEdges: [],
    hasPendingChanges: false,
    isAIGenerating: false,

    // AI Undo Snapshot
    undoSnapshot: null,

    // Canvas Chat Panel State
    isCanvasChatOpen: false,
};

// ============ Store ============
export const useWorkflowStore = create<WorkflowStore>((set, get) => ({
    ...initialState,

    // Actions - with smart dirty detection
    setNodes: (nodes) => set((state) => ({
        nodes,
        // Only mark dirty if values actually differ from last saved
        isDirty: state.isDirty || !nodesEqual(nodes, state.lastSavedNodes),
    })),
    setEdges: (edges) => set((state) => ({
        edges,
        // Only mark dirty if values actually differ from last saved
        isDirty: state.isDirty || !edgesEqual(edges, state.lastSavedEdges),
    })),

    addNode: (node) => set((state) => ({ nodes: [...state.nodes, node], isDirty: true })),

    updateNodeData: (nodeId, data) => set((state) => ({
        nodes: state.nodes.map((node) =>
            node.id === nodeId
                ? { ...node, data: { ...node.data, ...data } }
                : node
        ),
        isDirty: true,
    })),

    removeNode: (nodeId) => set((state) => ({
        nodes: state.nodes.filter((node) => node.id !== nodeId),
        edges: state.edges.filter(
            (edge) => edge.source !== nodeId && edge.target !== nodeId
        ),
        isDirty: true,
    })),

    setSelectedNodeId: (nodeId) => set({ selectedNodeId: nodeId }),

    setActiveNodeId: (nodeId) => set({ activeNodeId: nodeId }),

    setNodeStatus: (nodeId, status) => set((state) => ({
        nodeStatusMap: { ...state.nodeStatusMap, [nodeId]: status }
    })),

    setIsExecuting: (isExecuting) => set({ isExecuting }),

    setExecutionResult: (nodeId, result) => set((state) => ({
        executionResults: { ...state.executionResults, [nodeId]: result },
        nodeStatusMap: { ...state.nodeStatusMap, [nodeId]: result.status }
    })),

    setTestRunOpen: (testRunOpen) => set({ testRunOpen }),

    resetExecution: () => set({
        executionResults: {},
        isExecuting: false,
        activeNodeId: null,
        nodeStatusMap: {}
    }),

    reset: () => set(initialState),

    // Workflow identity actions
    initializeForWorkflow: (workflowId, nodes, edges) => {
        set({
            currentWorkflowId: workflowId,
            nodes,
            edges,
            isDirty: false,  // Critical: Don't mark dirty on init
            lastSavedNodes: nodes,
            lastSavedEdges: edges,
            selectedNodeId: null,
            activeNodeId: null,
            executionResults: {},
            nodeStatusMap: {},
            pendingNodes: [],
            pendingEdges: [],
            hasPendingChanges: false,
        });
    },

    isDataForWorkflow: (workflowId: string): boolean => {
        return get().currentWorkflowId === workflowId;
    },

    // Unsaved changes tracking actions
    markDirty: () => set({ isDirty: true }),
    markClean: () => set({ isDirty: false }),
    setLastSaved: (nodes, edges) => set({ lastSavedNodes: nodes, lastSavedEdges: edges, isDirty: false }),

    // Version viewing actions
    setViewingVersion: (versionId, versionNumber, isHistorical) => set({
        viewingVersionId: versionId,
        viewingVersionNumber: versionNumber,
        isViewingHistorical: isHistorical,
    }),
    setActiveVersionId: (versionId) => set({ activeVersionId: versionId }),
    switchToDraft: () => set({
        viewingVersionId: null,
        viewingVersionNumber: null,
        isViewingHistorical: false,
    }),

    // Sync actions - for FlowEditor sync without marking dirty
    syncNodes: (nodes) => set({ nodes }),
    syncEdges: (edges) => set({ edges }),

    // AI Workflow Builder - Pending State Actions
    setPending: (pendingNodes, pendingEdges) => set({
        pendingNodes,
        pendingEdges,
        hasPendingChanges: pendingNodes.length > 0 || pendingEdges.length > 0,
    }),

    applyPending: () => set((state) => {
        // Merge pending nodes/edges into main state
        // Pending nodes may have new IDs or modify existing nodes
        const existingNodeIds = new Set(state.nodes.map((n) => n.id));
        const newNodes = state.pendingNodes.filter((n) => !existingNodeIds.has(n.id));
        const updatedNodes = state.nodes.map((node) => {
            const pendingNode = state.pendingNodes.find((n) => n.id === node.id);
            return pendingNode || node;
        });

        return {
            nodes: [...updatedNodes, ...newNodes],
            edges: [...state.edges, ...state.pendingEdges.filter(
                (e) => !state.edges.some((existing) => existing.id === e.id)
            )],
            pendingNodes: [],
            pendingEdges: [],
            hasPendingChanges: false,
            isDirty: true,
        };
    }),

    discardPending: () => set({
        pendingNodes: [],
        pendingEdges: [],
        hasPendingChanges: false,
    }),

    lockForAI: () => set({ isAIGenerating: true }),

    unlockFromAI: () => set({ isAIGenerating: false }),

    // AI Undo Actions
    setUndoSnapshot: (snapshot) => set({ undoSnapshot: snapshot }),
    undoLastAIChange: () => set((state) => {
        if (!state.undoSnapshot) return state;
        return {
            nodes: state.undoSnapshot.nodes,
            edges: state.undoSnapshot.edges,
            undoSnapshot: null,
            isDirty: true,
        };
    }),

    // Canvas Chat Panel Actions
    toggleCanvasChat: () => set((state) => ({ isCanvasChatOpen: !state.isCanvasChatOpen })),
    setCanvasChatOpen: (open) => set({ isCanvasChatOpen: open }),
}));

// ============ Selectors ============
// Use these selectors to prevent unnecessary re-renders
// Components will only re-render when the specific state they subscribe to changes

export const selectNodes = (state: WorkflowStore) => state.nodes;
export const selectEdges = (state: WorkflowStore) => state.edges;
export const selectSelectedNodeId = (state: WorkflowStore) => state.selectedNodeId;
export const selectActiveNodeId = (state: WorkflowStore) => state.activeNodeId;
export const selectIsExecuting = (state: WorkflowStore) => state.isExecuting;
export const selectExecutionResults = (state: WorkflowStore) => state.executionResults;
export const selectNodeStatusMap = (state: WorkflowStore) => state.nodeStatusMap;

// Unsaved changes selectors
export const selectIsDirty = (state: WorkflowStore) => state.isDirty;

// Version viewing selectors
export const selectViewingVersionId = (state: WorkflowStore) => state.viewingVersionId;
export const selectViewingVersionNumber = (state: WorkflowStore) => state.viewingVersionNumber;
export const selectIsViewingHistorical = (state: WorkflowStore) => state.isViewingHistorical;
export const selectActiveVersionId = (state: WorkflowStore) => state.activeVersionId;

// Workflow identity selectors
export const selectCurrentWorkflowId = (state: WorkflowStore) => state.currentWorkflowId;

// AI Workflow Builder selectors
export const selectPendingNodes = (state: WorkflowStore) => state.pendingNodes;
export const selectPendingEdges = (state: WorkflowStore) => state.pendingEdges;
export const selectHasPendingChanges = (state: WorkflowStore) => state.hasPendingChanges;
export const selectIsAIGenerating = (state: WorkflowStore) => state.isAIGenerating;

// AI Undo selectors
export const selectUndoSnapshot = (state: WorkflowStore) => state.undoSnapshot;

// Canvas Chat Panel selector
export const selectIsCanvasChatOpen = (state: WorkflowStore) => state.isCanvasChatOpen;

// Derived selectors
export const selectSelectedNode = (state: WorkflowStore) =>
    state.nodes.find((n) => n.id === state.selectedNodeId) || null;

export const selectNodeById = (nodeId: string) => (state: WorkflowStore) =>
    state.nodes.find((n) => n.id === nodeId);

export const selectNodeStatus = (nodeId: string) => (state: WorkflowStore) =>
    state.nodeStatusMap[nodeId];

export const selectExecutionResult = (nodeId: string) => (state: WorkflowStore) =>
    state.executionResults[nodeId];

// Action selectors - only subscribe to specific actions
export const selectActions = (state: WorkflowStore) => ({
    setNodes: state.setNodes,
    setEdges: state.setEdges,
    addNode: state.addNode,
    updateNodeData: state.updateNodeData,
    removeNode: state.removeNode,
    setSelectedNodeId: state.setSelectedNodeId,
    setActiveNodeId: state.setActiveNodeId,
    setNodeStatus: state.setNodeStatus,
    setIsExecuting: state.setIsExecuting,
    setExecutionResult: state.setExecutionResult,
    setTestRunOpen: state.setTestRunOpen,
    resetExecution: state.resetExecution,
    reset: state.reset,
    // Workflow identity actions
    initializeForWorkflow: state.initializeForWorkflow,
    isDataForWorkflow: state.isDataForWorkflow,
    // Version viewing actions
    setViewingVersion: state.setViewingVersion,
    setActiveVersionId: state.setActiveVersionId,
    switchToDraft: state.switchToDraft,
    // Sync actions
    syncNodes: state.syncNodes,
    syncEdges: state.syncEdges,
    // AI Workflow Builder actions
    setPending: state.setPending,
    applyPending: state.applyPending,
    discardPending: state.discardPending,
    lockForAI: state.lockForAI,
    unlockFromAI: state.unlockFromAI,
    // AI Undo actions
    setUndoSnapshot: state.setUndoSnapshot,
    undoLastAIChange: state.undoLastAIChange,
});

// ============ Custom Hooks ============
// These hooks provide optimized subscriptions for common use cases

/**
 * Use this hook when you only need workflow actions without subscribing to state
 * Prevents re-renders when state changes
 */
export const useWorkflowActions = () => useWorkflowStore(useShallow(selectActions));

/**
 * Use this hook for flow editor state (nodes + edges)
 * Only re-renders when nodes or edges change
 */
export const useWorkflowFlow = () => useWorkflowStore(
    useShallow((state: WorkflowStore) => ({
        nodes: state.nodes,
        edges: state.edges,
        setNodes: state.setNodes,
        setEdges: state.setEdges,
    }))
);

/**
 * Use this hook for execution monitoring
 * Only re-renders when execution state changes
 */
export const useWorkflowExecution = () => useWorkflowStore(
    useShallow((state: WorkflowStore) => ({
        isExecuting: state.isExecuting,
        executionResults: state.executionResults,
        nodeStatusMap: state.nodeStatusMap,
        setIsExecuting: state.setIsExecuting,
        setExecutionResult: state.setExecutionResult,
        resetExecution: state.resetExecution,
    }))
);

/**
 * Use this hook for node selection
 * Only re-renders when selected node changes
 */
export const useWorkflowSelection = () => useWorkflowStore(
    useShallow((state: WorkflowStore) => ({
        selectedNodeId: state.selectedNodeId,
        selectedNode: state.nodes.find((n) => n.id === state.selectedNodeId) || null,
        setSelectedNodeId: state.setSelectedNodeId,
    }))
);

/**
 * Use this hook for AI workflow builder pending state
 * Only re-renders when pending state changes
 */
export const useWorkflowPending = () => useWorkflowStore(
    useShallow((state: WorkflowStore) => ({
        pendingNodes: state.pendingNodes,
        pendingEdges: state.pendingEdges,
        hasPendingChanges: state.hasPendingChanges,
        isAIGenerating: state.isAIGenerating,
        setPending: state.setPending,
        applyPending: state.applyPending,
        discardPending: state.discardPending,
        lockForAI: state.lockForAI,
        unlockFromAI: state.unlockFromAI,
    }))
);

/**
 * Use this hook for version viewing state
 * Only re-renders when version state changes
 */
export const useWorkflowVersion = () => useWorkflowStore(
    useShallow((state: WorkflowStore) => ({
        viewingVersionId: state.viewingVersionId,
        viewingVersionNumber: state.viewingVersionNumber,
        isViewingHistorical: state.isViewingHistorical,
        activeVersionId: state.activeVersionId,
        setViewingVersion: state.setViewingVersion,
        setActiveVersionId: state.setActiveVersionId,
        switchToDraft: state.switchToDraft,
    }))
);

/**
 * Use this hook for dirty state tracking
 * Only re-renders when dirty state changes
 */
export const useWorkflowDirty = () => useWorkflowStore(
    useShallow((state: WorkflowStore) => ({
        isDirty: state.isDirty,
        markDirty: state.markDirty,
        markClean: state.markClean,
        setLastSaved: state.setLastSaved,
    }))
);
