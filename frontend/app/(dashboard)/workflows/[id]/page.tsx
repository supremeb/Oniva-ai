'use client';

import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { Suspense, lazy, useState, useEffect, useRef, useCallback } from 'react';
import { GenerativeO } from '@/components/ui/GenerativeO';
import {
    ArrowLeft,
    PencilSimple,
    Check,
    X,
    FloppyDisk,
    Rocket,
    CheckCircle,
    CircleNotch,
    ClockCounterClockwise,
    Play,
    ArrowCounterClockwise,
    CaretDown,
    File,
    Circle,
    Eye,
    LockSimple,
    ArrowBendUpLeft,
    CalendarBlank,
} from '@phosphor-icons/react';
import { formatDistanceToNow } from 'date-fns';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useWorkflow, useUpdateWorkflow, useCreateWorkflow } from '@/hooks/useWorkflow';
import { useWorkflowStore, useWorkflowVersion as useWorkflowVersionStore } from '@/lib/stores/workflow';
import { useActiveWorkflowVersion, useCreateWorkflowVersion, useActivateWorkflowVersion, useWorkflowVersions, useWorkflowVersion as useWorkflowVersionApi, useDraftStatus, usePublishWorkflow } from '@/hooks/useWorkflowVersions';
import { PublishDialog } from '@/components/workflow/PublishDialog';
import { toast } from 'sonner';
import { BottomPanelLayout } from '@/components/workflow/bottom-panel';
import { UnsavedChangesDialog } from '@/components/workflow/UnsavedChangesDialog';

// Import ReactFlowProvider synchronously to avoid race conditions with lazy-loaded FlowEditor
import { ReactFlowProvider } from '@xyflow/react';

// Lazy load heavy components
const FlowEditor = lazy(() =>
    import('@/components/workflow/FlowEditor').then(mod => ({ default: mod.FlowEditor }))
);
const WorkflowChatPanel = lazy(() =>
    import('@/components/workflow/WorkflowChatPanel').then(mod => ({ default: mod.WorkflowChatPanel }))
);
const WorkflowVersionsPanel = lazy(() =>
    import('@/components/workflow/WorkflowVersionsPanel').then(mod => ({ default: mod.WorkflowVersionsPanel }))
);

function FlowEditorSkeleton() {
    return (
        <div className="flex items-center justify-center h-full bg-muted/10">
            <div className="text-center space-y-4">
                <div className="flex justify-center">
                    <GenerativeO size={48} speed={1} animating={true} />
                </div>
                <p className="text-muted-foreground">Loading workflow editor...</p>
            </div>
        </div>
    );
}

type EditorTab = 'builder' | 'chat' | 'versions';

export default function WorkflowEditorPage() {
    const params = useParams();
    const router = useRouter();
    const workflowId = params.id as string;
    const isLocalWorkflow = workflowId.startsWith('local-');
    const [activeTab, setActiveTab] = useState<EditorTab>('builder');
    const [isEditingName, setIsEditingName] = useState(false);
    const [editedName, setEditedName] = useState('');
    const inputRef = useRef<HTMLInputElement>(null);

    const { data: workflow, refetch: refetchWorkflow } = useWorkflow(workflowId);
    const updateWorkflowMutation = useUpdateWorkflow();
    const createWorkflowMutation = useCreateWorkflow();
    const workflowName = workflow?.name || 'Untitled Workflow';
    const [syncingToServer, setSyncingToServer] = useState(false);

    // Versioning hooks
    const { data: activeVersion, refetch: refetchActiveVersion } = useActiveWorkflowVersion(workflowId);
    const { data: allVersions, refetch: refetchVersions } = useWorkflowVersions(workflowId, { includeDrafts: true, limit: 20 });
    const { data: draftStatus, refetch: refetchDraftStatus } = useDraftStatus(workflowId);
    const createVersionMutation = useCreateWorkflowVersion();
    const activateVersionMutation = useActivateWorkflowVersion();
    const publishWorkflowMutation = usePublishWorkflow();
    const [savingDraft, setSavingDraft] = useState(false);
    const [publishing, setPublishing] = useState(false);
    const [lastSavedAt, setLastSavedAt] = useState<Date | null>(null);
    const [showPublishDialog, setShowPublishDialog] = useState(false);

    // Version viewing state (from store)
    const {
        viewingVersionId,
        viewingVersionNumber,
        isViewingHistorical,
        setViewingVersion,
        switchToDraft,
    } = useWorkflowVersionStore();

    // Fetch the version being viewed (only when viewing a specific version)
    const { data: viewingVersionData } = useWorkflowVersionApi(
        workflowId,
        viewingVersionId || ''
    );

    // Execution state and editor state from workflow store
    const { isExecuting, resetExecution, setTestRunOpen, testRunOpen, nodes, edges, isDirty, markClean, syncNodes, syncEdges, setLastSaved } = useWorkflowStore();

    // Unsaved changes dialog state
    const [showUnsavedDialog, setShowUnsavedDialog] = useState(false);
    const [pendingNavigation, setPendingNavigation] = useState<string | null>(null);

    useEffect(() => {
        if (isEditingName && inputRef.current) {
            inputRef.current.focus();
            inputRef.current.select();
        }
    }, [isEditingName]);

    // Reset viewing state when workflow changes
    useEffect(() => {
        // When workflowId changes, reset to draft view
        switchToDraft();
    }, [workflowId, switchToDraft]);

    // Handle tab switching
    const handleTabChange = useCallback((newTab: EditorTab) => {
        setActiveTab(newTab);
    }, []);

    // Store original draft state when switching to historical version
    const draftNodesRef = useRef<typeof nodes | null>(null);
    const draftEdgesRef = useRef<typeof edges | null>(null);
    const loadedVersionIdRef = useRef<string | null>(null);

    // Load version spec when viewing a historical version
    useEffect(() => {
        if (isViewingHistorical && viewingVersionData?.spec && viewingVersionId) {
            // Only load once per version (prevent infinite loop)
            if (loadedVersionIdRef.current === viewingVersionId) {
                return;
            }
            loadedVersionIdRef.current = viewingVersionId;

            // Load the historical version's spec
            type NodeSpec = { id: string; type: string; position: { x: number; y: number }; data: Record<string, unknown> };
            type EdgeSpec = { id: string; source: string; target: string; sourceHandle?: string; targetHandle?: string };
            const versionNodes = (viewingVersionData.spec.nodes as NodeSpec[]).map((node) => ({
                id: node.id,
                type: node.type,
                position: node.position,
                data: node.data,
            }));
            const versionEdges = (viewingVersionData.spec.edges as EdgeSpec[]).map((edge) => ({
                id: edge.id,
                source: edge.source,
                target: edge.target,
                sourceHandle: edge.sourceHandle,
                targetHandle: edge.targetHandle,
            }));

            syncNodes(versionNodes);
            syncEdges(versionEdges);
        }
    }, [isViewingHistorical, viewingVersionData, viewingVersionId, syncNodes, syncEdges]);

    // Restore draft state when switching back to draft
    // FIXED: Use Zustand as source of truth, not stale TanStack Query cache
    const handleSwitchToDraft = useCallback(() => {
        // Reset the loaded version tracker
        loadedVersionIdRef.current = null;

        if (draftNodesRef.current && draftEdgesRef.current) {
            // Restore from saved draft refs (captured when switching to historical)
            syncNodes(draftNodesRef.current);
            syncEdges(draftEdgesRef.current);
        } else {
            // No saved draft refs - check if Zustand already has state
            // Zustand is the source of truth for current draft
            const storeState = useWorkflowStore.getState();
            if (storeState.nodes.length > 0 && storeState.currentWorkflowId === workflowId) {
                // Zustand already has state for this workflow - it IS the draft, no action needed
                // This prevents overwriting user's unsaved changes with stale cache
            } else if (workflow?.spec) {
                // Edge case: Store is empty - fetch from workflow spec
                // This can happen on initial load or after a full page refresh
                const workflowNodes = workflow.spec.nodes.map((node) => ({
                    id: node.id,
                    type: node.type,
                    position: node.position,
                    data: node.data,
                }));
                const workflowEdges = workflow.spec.edges.map((edge) => ({
                    id: edge.id,
                    source: edge.source,
                    target: edge.target,
                    sourceHandle: edge.sourceHandle,
                    targetHandle: edge.targetHandle,
                }));
                syncNodes(workflowNodes);
                syncEdges(workflowEdges);
            }
        }
        draftNodesRef.current = null;
        draftEdgesRef.current = null;
        switchToDraft();
    }, [workflow, workflowId, syncNodes, syncEdges, switchToDraft]);

    // View a specific version (read-only mode)
    const handleViewVersion = useCallback((versionId: string, versionNumber: number, isActive: boolean) => {
        // If viewing the active version, just switch to draft (current state)
        if (isActive) {
            handleSwitchToDraft();
            return;
        }

        // Save current draft state before switching (get fresh state from store)
        if (!isViewingHistorical && draftNodesRef.current === null) {
            const currentState = useWorkflowStore.getState();
            draftNodesRef.current = currentState.nodes;
            draftEdgesRef.current = currentState.edges;
        }

        // Set viewing state
        setViewingVersion(versionId, versionNumber, true);
    }, [isViewingHistorical, setViewingVersion, handleSwitchToDraft]);

    const handleStartEditing = () => {
        setEditedName(workflowName);
        setIsEditingName(true);
    };

    const handleSaveName = async () => {
        if (!editedName.trim()) {
            setIsEditingName(false);
            return;
        }

        try {
            await updateWorkflowMutation.mutateAsync({
                id: workflowId,
                name: editedName.trim(),
            });
            toast('Name updated', { description: 'Workflow name has been saved.' });
        } catch {
            toast.error('Error', { description: 'Failed to update workflow name.' });
        }
        setIsEditingName(false);
    };

    const handleCancelEditing = () => {
        setIsEditingName(false);
        setEditedName('');
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter') {
            handleSaveName();
        } else if (e.key === 'Escape') {
            handleCancelEditing();
        }
    };

    // Navigate with unsaved changes check - ALWAYS warn if dirty, regardless of local/backend
    const navigateWithCheck = useCallback((path: string) => {
        if (isDirty) {
            setPendingNavigation(path);
            setShowUnsavedDialog(true);
        } else {
            router.push(path);
        }
    }, [isDirty, router]);

    // Handle discard changes
    const handleDiscardChanges = useCallback(() => {
        markClean();
        setShowUnsavedDialog(false);
        if (pendingNavigation) {
            router.push(pendingNavigation);
            setPendingNavigation(null);
        }
    }, [markClean, pendingNavigation, router]);

    // Handle save draft from dialog - syncs to database then navigates
    const handleSaveDraftAndNavigate = useCallback(async () => {
        if (savingDraft || syncingToServer) return;
        setSavingDraft(true);

        try {
            let targetWorkflowId = workflowId;

            const spec = {
                nodes: nodes.map((node) => ({
                    id: node.id,
                    type: (node.type || 'start') as import('@/types/api').WorkflowNodeType,
                    position: node.position,
                    data: node.data as Record<string, unknown>,
                })),
                edges: edges.map((edge) => ({
                    id: edge.id,
                    source: edge.source,
                    target: edge.target,
                    sourceHandle: edge.sourceHandle ?? undefined,
                    targetHandle: edge.targetHandle ?? undefined,
                })),
            };

            // For local workflows, sync to database first
            if (workflowId.startsWith('local-')) {
                setSyncingToServer(true);

                const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/v1/workflows`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    credentials: 'include',
                    body: JSON.stringify({ name: workflowName, spec }),
                });

                if (!response.ok) {
                    if (response.status === 401) {
                        toast.error('Session Expired', { description: 'Please log in again.' });
                    } else {
                        toast.error('Error', { description: 'Failed to save. Discard to continue.' });
                    }
                    setSavingDraft(false);
                    setSyncingToServer(false);
                    return;
                }

                const newWorkflow = await response.json();
                targetWorkflowId = newWorkflow.id;

                // Delete local workflow from localStorage
                const storedWorkflows = localStorage.getItem('oniva-workflows');
                if (storedWorkflows) {
                    const workflows = JSON.parse(storedWorkflows);
                    const filtered = workflows.filter((w: { id: string }) => w.id !== workflowId);
                    localStorage.setItem('oniva-workflows', JSON.stringify(filtered));
                }

                setSyncingToServer(false);
            } else {
                // For backend workflows, update existing
                if (nodes.length > 0) {
                    await updateWorkflowMutation.mutateAsync({
                        id: workflowId,
                        spec,
                    });
                }
            }

            // Create version
            await createVersionMutation.mutateAsync({
                workflowId: targetWorkflowId,
                request: {
                    is_draft: true,
                    description: 'Draft saved before navigating',
                },
            });

            markClean();
            toast('Draft Saved', { description: 'Saved to database' });

            setShowUnsavedDialog(false);
            if (pendingNavigation) {
                router.push(pendingNavigation);
                setPendingNavigation(null);
            }
        } catch (error: unknown) {
            const apiError = error as { status?: number };
            if (apiError?.status === 409) {
                // No changes, just navigate
                markClean();
                setShowUnsavedDialog(false);
                if (pendingNavigation) {
                    router.push(pendingNavigation);
                    setPendingNavigation(null);
                }
            } else if (apiError?.status === 401) {
                toast.error('Session Expired', { description: 'Please log in again.' });
            } else {
                toast.error('Error', { description: 'Failed to save. Discard to continue.' });
            }
        } finally {
            setSavingDraft(false);
            setSyncingToServer(false);
        }
    }, [workflowId, workflowName, nodes, edges, updateWorkflowMutation, createVersionMutation, markClean, pendingNavigation, router, savingDraft, syncingToServer]);

    // Warn on page unload if there are unsaved changes - ALWAYS warn if dirty
    useEffect(() => {
        const handleBeforeUnload = (e: BeforeUnloadEvent) => {
            if (isDirty) {
                e.preventDefault();
                e.returnValue = '';
                return '';
            }
        };

        window.addEventListener('beforeunload', handleBeforeUnload);
        return () => window.removeEventListener('beforeunload', handleBeforeUnload);
    }, [isDirty]);

    // Intercept browser back/forward navigation - ALWAYS warn if dirty
    useEffect(() => {
        const handlePopState = () => {
            if (isDirty) {
                // Push current state back to prevent navigation
                window.history.pushState(null, '', window.location.href);
                // Show unsaved changes dialog
                setPendingNavigation('/workflows');
                setShowUnsavedDialog(true);
            }
        };

        window.addEventListener('popstate', handlePopState);
        return () => window.removeEventListener('popstate', handlePopState);
    }, [isDirty]);

    // Save as draft version - syncs to database for all workflows
    const handleSaveDraft = useCallback(async () => {
        if (savingDraft || syncingToServer) return;
        setSavingDraft(true);

        try {
            let targetWorkflowId = workflowId;

            // Get current nodes/edges from the store
            const currentState = useWorkflowStore.getState();
            const currentNodes = currentState.nodes;
            const currentEdges = currentState.edges;

            const spec = {
                nodes: currentNodes.map((node) => ({
                    id: node.id,
                    type: (node.type || 'start') as import('@/types/api').WorkflowNodeType,
                    position: node.position,
                    data: node.data as Record<string, unknown>,
                })),
                edges: currentEdges.map((edge) => ({
                    id: edge.id,
                    source: edge.source,
                    target: edge.target,
                    sourceHandle: edge.sourceHandle ?? undefined,
                    targetHandle: edge.targetHandle ?? undefined,
                })),
            };

            // For local workflows, sync to database first
            if (workflowId.startsWith('local-')) {
                setSyncingToServer(true);

                // Direct API call to create workflow (avoid fallback behavior)
                const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/v1/workflows`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    credentials: 'include',
                    body: JSON.stringify({ name: workflowName, spec }),
                });

                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({}));
                    if (response.status === 401) {
                        toast.error('Session Expired', { description: 'Please log in again to save.' });
                    } else {
                        toast.error('Error', { description: errorData.detail || 'Failed to save. Try again.' });
                    }
                    setSavingDraft(false);
                    setSyncingToServer(false);
                    return;
                }

                const newWorkflow = await response.json();
                targetWorkflowId = newWorkflow.id;

                // Update URL to new workflow ID
                window.history.replaceState({}, '', `/workflows/${newWorkflow.id}`);

                // Delete local workflow from localStorage
                const storedWorkflows = localStorage.getItem('oniva-workflows');
                if (storedWorkflows) {
                    const workflows = JSON.parse(storedWorkflows);
                    const filtered = workflows.filter((w: { id: string }) => w.id !== workflowId);
                    localStorage.setItem('oniva-workflows', JSON.stringify(filtered));
                }

                setSyncingToServer(false);
            } else {
                // For backend workflows, update existing
                if (nodes.length > 0) {
                    await updateWorkflowMutation.mutateAsync({
                        id: workflowId,
                        spec,
                    });
                }
            }

            // Create version
            await createVersionMutation.mutateAsync({
                workflowId: targetWorkflowId,
                request: {
                    is_draft: true,
                    description: 'Draft saved from editor',
                },
            });

            toast('Draft Saved', { description: 'Saved to database' });
            markClean();
            setLastSavedAt(new Date());
            // Refresh version list and active version to show new draft immediately
            await Promise.all([refetchVersions(), refetchActiveVersion()]);
        } catch (error: unknown) {
            const apiError = error as { message?: string; status?: number };
            if (apiError?.status === 409) {
                toast('No Changes', { description: 'No changes detected since last version' });
                markClean();
                setLastSavedAt(new Date());
            } else if (apiError?.status === 401) {
                toast.error('Session Expired', { description: 'Please log in again to save.' });
            } else {
                toast.error('Error', { description: 'Failed to save draft. Try again.' });
            }
        } finally {
            setSavingDraft(false);
            setSyncingToServer(false);
        }
    }, [workflowId, workflowName, createVersionMutation, updateWorkflowMutation, toast, refetchActiveVersion, refetchVersions, savingDraft, syncingToServer, markClean]);

    // Publish with semantic versioning - opens dialog, then calls publish API
    const handlePublish = useCallback(async (versionTag: string, description: string) => {
        if (publishing || syncingToServer) return;
        setPublishing(true);

        try {
            let targetWorkflowId = workflowId;

            // Get current nodes/edges from the store
            const currentState = useWorkflowStore.getState();
            const currentNodes = currentState.nodes;
            const currentEdges = currentState.edges;

            const spec = {
                nodes: currentNodes.map((node) => ({
                    id: node.id,
                    type: (node.type || 'start') as import('@/types/api').WorkflowNodeType,
                    position: node.position,
                    data: node.data as Record<string, unknown>,
                })),
                edges: currentEdges.map((edge) => ({
                    id: edge.id,
                    source: edge.source,
                    target: edge.target,
                    sourceHandle: edge.sourceHandle ?? undefined,
                    targetHandle: edge.targetHandle ?? undefined,
                })),
            };

            // For local workflows, sync to server first
            if (workflowId.startsWith('local-')) {
                setSyncingToServer(true);

                // Direct API call to create workflow (bypass the fallback)
                const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/v1/workflows`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    credentials: 'include',
                    body: JSON.stringify({ name: workflowName, spec }),
                });

                if (!response.ok) {
                    const errorData = await response.json().catch(() => ({}));
                    if (response.status === 401) {
                        toast.error('Session Expired', { description: 'Please log in again to publish.' });
                    } else {
                        toast.error('Cannot Publish', { description: errorData.detail || 'Server error. Try again.' });
                    }
                    setPublishing(false);
                    setSyncingToServer(false);
                    throw new Error(errorData.detail || 'Failed to sync workflow');
                }

                const newWorkflow = await response.json();
                targetWorkflowId = newWorkflow.id;

                // Update URL to new workflow ID
                window.history.replaceState({}, '', `/workflows/${newWorkflow.id}`);

                // Delete local workflow from localStorage
                const storedWorkflows = localStorage.getItem('oniva-workflows');
                if (storedWorkflows) {
                    const workflows = JSON.parse(storedWorkflows);
                    const filtered = workflows.filter((w: { id: string }) => w.id !== workflowId);
                    localStorage.setItem('oniva-workflows', JSON.stringify(filtered));
                }

                setSyncingToServer(false);
            } else {
                // For backend workflows, save current state first
                if (nodes.length > 0) {
                    await updateWorkflowMutation.mutateAsync({
                        id: workflowId,
                        spec,
                    });
                }
            }

            // Publish with semantic versioning
            const version = await publishWorkflowMutation.mutateAsync({
                workflowId: targetWorkflowId,
                request: {
                    version_tag: versionTag,
                    description: description || undefined,
                },
            });

            toast('Published', { description: `Version ${version.version_tag || version.version_number} is now active` });
            markClean();
            setLastSavedAt(new Date());
            // Refresh version list, active version, and draft status
            await Promise.all([refetchVersions(), refetchActiveVersion(), refetchDraftStatus()]);
        } catch (error: unknown) {
            const apiError = error as { message?: string; status?: number };
            if (apiError?.status === 409) {
                toast.error('Version Conflict', { description: apiError.message || 'This version tag already exists' });
            } else if (apiError?.status === 401) {
                toast.error('Session Expired', { description: 'Please log in again to publish.' });
            } else if (apiError?.message) {
                throw error; // Re-throw to let PublishDialog handle it
            } else {
                toast.error('Error', { description: 'Failed to publish. Try again.' });
            }
            throw error;
        } finally {
            setPublishing(false);
            setSyncingToServer(false);
        }
    }, [workflowId, workflowName, publishWorkflowMutation, updateWorkflowMutation, refetchActiveVersion, refetchVersions, refetchDraftStatus, publishing, syncingToServer, markClean]);

    // Callback when version is activated from panel
    const handleVersionActivated = useCallback(() => {
        refetchActiveVersion();
    }, [refetchActiveVersion]);

    // Callback when rollback happens from panel
    const handleRollback = useCallback(() => {
        refetchWorkflow();
        refetchActiveVersion();
    }, [refetchWorkflow, refetchActiveVersion]);

    return (
        <div className="flex flex-col h-full overflow-hidden">
            <header className="h-14 px-4 border-b bg-background flex items-center justify-between shrink-0 gap-4">
                {/* Left: Back + Name + Version */}
                <div className="flex items-center gap-3 min-w-0 shrink-0">
                    <Button
                        variant="ghost"
                        size="icon"
                        className="shrink-0"
                        onClick={() => navigateWithCheck('/workflows')}
                    >
                        <ArrowLeft className="h-4 w-4" />
                    </Button>

                    {isEditingName ? (
                        <div className="flex items-center gap-2">
                            <Input
                                ref={inputRef}
                                value={editedName}
                                onChange={(e) => setEditedName(e.target.value)}
                                onKeyDown={handleKeyDown}
                                onBlur={handleSaveName}
                                className="h-8 w-48 text-sm"
                                placeholder="Workflow name"
                            />
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7"
                                onClick={handleSaveName}
                            >
                                <Check className="h-4 w-4 text-green-600" />
                            </Button>
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7"
                                onClick={handleCancelEditing}
                            >
                                <X className="h-4 w-4 text-muted-foreground" />
                            </Button>
                        </div>
                    ) : (
                        <div className="flex items-center gap-2 min-w-0">
                            <button
                                onClick={handleStartEditing}
                                className="flex items-center gap-1.5 group min-w-0"
                            >
                                <span className="text-card-title truncate max-w-[150px]">
                                    {workflowName}
                                    {isDirty && <span className="text-amber-500 ml-0.5">*</span>}
                                </span>
                                <PencilSimple className="h-3.5 w-3.5 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
                            </button>
                            {/* Save status indicator - shown for all workflows */}
                            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                                {(savingDraft || syncingToServer) && (
                                    <>
                                        <CircleNotch className="h-3 w-3 animate-spin" />
                                        <span>{syncingToServer ? 'Syncing...' : 'Saving...'}</span>
                                    </>
                                )}
                                {!savingDraft && !syncingToServer && lastSavedAt && !isDirty && (
                                    <>
                                        <Check className="h-3 w-3 text-green-600" />
                                        <span>Saved {formatDistanceToNow(lastSavedAt, { addSuffix: true })}</span>
                                    </>
                                )}
                                {!savingDraft && !syncingToServer && !lastSavedAt && isDirty && (
                                    <>
                                        <Circle className="h-2.5 w-2.5 text-amber-500" weight="fill" />
                                        <span>Unsaved</span>
                                    </>
                                )}
                            </div>
                            {/* Version dropdown */}
                            {!isLocalWorkflow && (
                                <DropdownMenu>
                                    <DropdownMenuTrigger asChild>
                                        <Button variant="ghost" size="sm" className="h-6 gap-1 px-2 text-xs">
                                            {isViewingHistorical ? (
                                                <>
                                                    <Eye className="h-3 w-3 text-blue-500" />
                                                    {(() => {
                                                        const version = allVersions?.find(v => v.version_number === viewingVersionNumber);
                                                        return version?.version_tag ? `v${version.version_tag}` : `v${viewingVersionNumber}`;
                                                    })()}
                                                    <Badge className="bg-blue-100 text-blue-700 h-4 text-[10px] ml-1">
                                                        READ ONLY
                                                    </Badge>
                                                </>
                                            ) : activeVersion ? (
                                                <>
                                                    <CheckCircle className="h-3 w-3 text-green-600" weight="fill" />
                                                    {activeVersion.version_tag ? `v${activeVersion.version_tag}` : `v${activeVersion.version_number}`}
                                                    <Badge className="bg-green-100 text-green-700 h-4 text-[10px] ml-1">
                                                        LIVE
                                                    </Badge>
                                                </>
                                            ) : (
                                                <>
                                                    <File className="h-3 w-3 text-muted-foreground" />
                                                    Draft
                                                </>
                                            )}
                                            <CaretDown className="h-3 w-3 text-muted-foreground" />
                                        </Button>
                                    </DropdownMenuTrigger>
                                    <DropdownMenuContent align="start" className="w-72">
                                        {/* Return to draft editing option (shown when viewing historical) */}
                                        {isViewingHistorical && (
                                            <>
                                                <DropdownMenuItem
                                                    className="flex items-center gap-2 py-2 text-primary"
                                                    onClick={handleSwitchToDraft}
                                                >
                                                    <ArrowBendUpLeft className="h-4 w-4" />
                                                    <span className="font-medium">Return to Draft</span>
                                                </DropdownMenuItem>
                                                <DropdownMenuSeparator />
                                            </>
                                        )}

                                        {/* Currently editing indicator */}
                                        {isDirty && !isViewingHistorical && (
                                            <>
                                                <div className="px-2 py-1.5 text-xs text-muted-foreground flex items-center gap-2">
                                                    <Circle className="h-2 w-2 text-amber-500" weight="fill" />
                                                    Editing (unsaved)
                                                </div>
                                                <DropdownMenuSeparator />
                                            </>
                                        )}

                                        {allVersions && allVersions.length > 0 ? (
                                            <>
                                                {/* Published versions */}
                                                <div className="px-2 py-1 text-xs text-muted-foreground font-medium">
                                                    Published Versions
                                                </div>
                                                {allVersions.filter((v) => !v.is_draft).map((version) => {
                                                    const isActive = version.id === activeVersion?.id;
                                                    const isViewing = version.id === viewingVersionId;
                                                    const displayVersion = version.version_tag ? `v${version.version_tag}` : `v${version.version_number}`;
                                                    return (
                                                        <DropdownMenuItem
                                                            key={version.id}
                                                            className="flex items-center justify-between py-2"
                                                            onClick={() => handleViewVersion(version.id, version.version_number, isActive)}
                                                        >
                                                            <div className="flex flex-col gap-0.5">
                                                                <div className="flex items-center gap-2">
                                                                    <span className="font-medium">{displayVersion}</span>
                                                                    {isActive && (
                                                                        <Badge className="bg-green-100 text-green-700 h-4 text-[10px]">
                                                                            LIVE
                                                                        </Badge>
                                                                    )}
                                                                    {isViewing && !isActive && (
                                                                        <Badge className="bg-blue-100 text-blue-700 h-4 text-[10px]">
                                                                            VIEWING
                                                                        </Badge>
                                                                    )}
                                                                </div>
                                                                <span className="text-xs text-muted-foreground">
                                                                    {formatDistanceToNow(new Date(version.created_at), { addSuffix: true })}
                                                                </span>
                                                            </div>
                                                            <Eye className="h-4 w-4 text-muted-foreground" />
                                                        </DropdownMenuItem>
                                                    );
                                                })}

                                                {/* Drafts */}
                                                {allVersions.some((v) => v.is_draft) && (
                                                    <>
                                                        <DropdownMenuSeparator />
                                                        <div className="px-2 py-1 text-xs text-muted-foreground font-medium">
                                                            Drafts
                                                        </div>
                                                        {allVersions.filter((v) => v.is_draft).map((draft) => (
                                                            <DropdownMenuItem
                                                                key={draft.id}
                                                                className="flex items-center justify-between py-2"
                                                                onClick={() => handleViewVersion(draft.id, draft.version_number, false)}
                                                            >
                                                                <div className="flex flex-col gap-0.5">
                                                                    <div className="flex items-center gap-2">
                                                                        <File className="h-3.5 w-3.5 text-muted-foreground" />
                                                                        <span>v{draft.version_tag || draft.version_number}</span>
                                                                        <Badge variant="outline" className="h-4 text-[10px]">
                                                                            Draft
                                                                        </Badge>
                                                                    </div>
                                                                    <span className="text-xs text-muted-foreground pl-5">
                                                                        {formatDistanceToNow(new Date(draft.created_at), { addSuffix: true })}
                                                                    </span>
                                                                </div>
                                                                <Eye className="h-4 w-4 text-muted-foreground" />
                                                            </DropdownMenuItem>
                                                        ))}
                                                    </>
                                                )}

                                                {/* View full history link */}
                                                <DropdownMenuSeparator />
                                                <DropdownMenuItem
                                                    className="text-xs text-primary gap-2"
                                                    onClick={() => setActiveTab('versions')}
                                                >
                                                    <ClockCounterClockwise className="h-3.5 w-3.5" />
                                                    View Full History
                                                </DropdownMenuItem>
                                            </>
                                        ) : (
                                            <DropdownMenuItem disabled className="text-muted-foreground">
                                                No versions yet
                                            </DropdownMenuItem>
                                        )}
                                    </DropdownMenuContent>
                                </DropdownMenu>
                            )}
                            {isLocalWorkflow && (
                                <Badge variant="outline" className="text-xs shrink-0 text-muted-foreground">
                                    Local
                                </Badge>
                            )}
                        </div>
                    )}
                </div>

                {/* Center: Tabs */}
                <div className="flex-1 flex justify-center">
                    <Tabs value={activeTab} onValueChange={(v) => handleTabChange(v as EditorTab)}>
                        <TabsList>
                            <TabsTrigger value="builder">Builder</TabsTrigger>
                            <TabsTrigger value="chat">Chat</TabsTrigger>
                            <TabsTrigger value="versions" className="gap-1.5" disabled={isLocalWorkflow}>
                                <ClockCounterClockwise className="h-3.5 w-3.5" />
                                Versions
                            </TabsTrigger>
                        </TabsList>
                    </Tabs>
                </div>

                {/* Right: Actions */}
                <div className="flex items-center gap-2 shrink-0">
                    {/* Schedules link */}
                    <Button
                        variant="ghost"
                        size="sm"
                        asChild
                        className="gap-1.5"
                    >
                        <Link href={`/schedules?workflow_id=${workflowId}`}>
                            <CalendarBlank className="h-4 w-4" />
                            Schedules
                        </Link>
                    </Button>

                    {/* Reset - only shown when executing or has executed */}
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={resetExecution}
                        disabled={isExecuting}
                        className="gap-1.5"
                    >
                        <ArrowCounterClockwise className="h-4 w-4" />
                        Reset
                    </Button>

                    {/* Save Draft - available for all workflows (disabled when viewing history) */}
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={handleSaveDraft}
                        disabled={savingDraft || publishing || syncingToServer || isExecuting || isViewingHistorical}
                        className="gap-1.5"
                    >
                        {savingDraft || syncingToServer ? (
                            <CircleNotch className="h-4 w-4 animate-spin" />
                        ) : (
                            <FloppyDisk className="h-4 w-4" />
                        )}
                        {syncingToServer ? 'Syncing...' : 'Save Draft'}
                    </Button>

                    {/* Execute - available for current version and historical versions */}
                    <Button
                        size="sm"
                        onClick={() => setTestRunOpen(true)}
                        disabled={isExecuting}
                        className="gap-1.5"
                    >
                        {isExecuting ? (
                            <CircleNotch className="h-4 w-4 animate-spin" />
                        ) : (
                            <Play className="h-4 w-4" weight="fill" />
                        )}
                        {isExecuting ? 'Running...' : 'Execute'}
                    </Button>

                    {/* Publish - available for all workflows (disabled when viewing history) */}
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setShowPublishDialog(true)}
                        disabled={publishing || savingDraft || syncingToServer || isExecuting || isViewingHistorical}
                        className="gap-1.5"
                    >
                        {publishing ? (
                            <CircleNotch className="h-4 w-4 animate-spin" />
                        ) : (
                            <Rocket className="h-4 w-4" />
                        )}
                        Publish
                    </Button>
                </div>
            </header>

            {/* Read-only banner when viewing historical version */}
            {isViewingHistorical && (
                <div className="bg-blue-50 dark:bg-blue-950 border-b border-blue-200 dark:border-blue-800 px-4 py-2 flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-blue-700 dark:text-blue-300">
                        <LockSimple className="h-4 w-4" weight="fill" />
                        <span>
                            Viewing <strong>Version {viewingVersionNumber}</strong> in read-only mode
                        </span>
                    </div>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={handleSwitchToDraft}
                        className="gap-1.5 h-7 text-blue-700 dark:text-blue-300 border-blue-300 dark:border-blue-700 hover:bg-blue-100 dark:hover:bg-blue-900"
                    >
                        <ArrowBendUpLeft className="h-3.5 w-3.5" />
                        Return to Draft
                    </Button>
                </div>
            )}

            <div className="flex-1 min-h-0">
                {/* Builder tab gets the bottom panel for Logs/Monitoring/API */}
                {activeTab === 'builder' && (
                    <BottomPanelLayout
                        workflowId={workflowId}
                        showPanel={true}
                    >
                        <Suspense fallback={<FlowEditorSkeleton />}>
                            <ReactFlowProvider key={workflowId}>
                                <FlowEditor workflowId={workflowId} readOnly={isViewingHistorical} />
                            </ReactFlowProvider>
                        </Suspense>
                    </BottomPanelLayout>
                )}

                {/* Chat and Versions tabs don't have bottom panel */}
                {activeTab === 'chat' && (
                    <Suspense fallback={<FlowEditorSkeleton />}>
                        <WorkflowChatPanel
                            workflowId={workflowId}
                            workflowName={workflowName}
                            nodes={nodes}  // Use Zustand nodes to reflect unsaved changes
                            edges={edges}  // Use Zustand edges to reflect unsaved changes
                            standalone
                        />
                    </Suspense>
                )}
                {activeTab === 'versions' && (
                    <Suspense fallback={<FlowEditorSkeleton />}>
                        <WorkflowVersionsPanel
                            workflowId={workflowId}
                            currentVersionId={activeVersion?.id}
                            onVersionActivated={handleVersionActivated}
                            onRollback={handleRollback}
                        />
                    </Suspense>
                )}
            </div>

            {/* Unsaved Changes Dialog */}
            <UnsavedChangesDialog
                open={showUnsavedDialog}
                onOpenChange={setShowUnsavedDialog}
                onDiscard={handleDiscardChanges}
                onSaveDraft={handleSaveDraftAndNavigate}
                isSaving={savingDraft}
            />

            {/* Publish Dialog with Semantic Versioning */}
            <PublishDialog
                open={showPublishDialog}
                onOpenChange={setShowPublishDialog}
                onPublish={handlePublish}
                suggestedVersions={draftStatus?.suggested_versions || { patch: '0.0.1', minor: '0.1.0', major: '1.0.0' }}
                currentVersion={draftStatus?.latest_published?.version_tag || null}
                isPublishing={publishing}
            />
        </div>
    );
}
