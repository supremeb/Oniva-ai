'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { formatDistanceToNow, parseISO } from 'date-fns';
import {
    Plus,
    Trash,
    Pencil,
    DotsThree,
    TreeStructure,
    CaretRight,
    SquaresFour,
    FileText,
    CheckCircle,
} from '@phosphor-icons/react';

import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
} from '@/components/ui/dialog';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Badge } from '@/components/ui/badge';
import { useWorkflows, useCreateWorkflow, useDeleteWorkflow } from '@/hooks/useWorkflow';
import { useWorkflowStore } from '@/lib/stores/workflow';
import { useActiveWorkflowVersion } from '@/hooks/useWorkflowVersions';
import { toast } from 'sonner';
import { ConfirmationDialog } from '@/components/ui/confirmation-dialog';
import type { WorkflowNode, WorkflowEdge } from '@/lib/api/workflows';
import {
    WORKFLOW_TEMPLATES,
    TEMPLATE_CATEGORIES,
    CATEGORY_ICONS,
    getTemplateCount,
    type WorkflowTemplate,
} from '@/lib/workflow-templates';
import type { Workflow } from '@/types/api';

// Workflow card component with version badge
function WorkflowCard({
    workflow,
    onDelete
}: {
    workflow: Workflow;
    onDelete: (id: string, e: React.MouseEvent) => void;
}) {
    const router = useRouter();
    const isLocal = workflow.id.startsWith('local-');
    const { data: activeVersion } = useActiveWorkflowVersion(workflow.id);

    return (
        <Link href={`/workflows/${workflow.id}`}>
            <div className="p-4 rounded-lg border border-border hover:border-foreground/20 transition-colors group">
                <div className="flex items-start justify-between mb-3">
                    <div className="w-8 h-8 rounded-lg bg-muted flex items-center justify-center">
                        <TreeStructure className="h-4 w-4 text-muted-foreground" weight="regular" />
                    </div>
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <button
                                aria-label={`More options for ${workflow.name}`}
                                className="p-1.5 rounded-md opacity-0 group-hover:opacity-100 hover:bg-muted transition-all"
                                onClick={(e) => e.stopPropagation()}
                            >
                                <DotsThree className="h-4 w-4 text-muted-foreground" weight="bold" aria-hidden="true" />
                            </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={(e) => { e.stopPropagation(); router.push(`/workflows/${workflow.id}`); }}>
                                <Pencil className="mr-2 h-4 w-4" />
                                Edit
                            </DropdownMenuItem>
                            <DropdownMenuItem
                                className="text-destructive focus:text-destructive"
                                onClick={(e) => onDelete(workflow.id, e as unknown as React.MouseEvent)}
                            >
                                <Trash className="mr-2 h-4 w-4" />
                                Delete
                            </DropdownMenuItem>
                        </DropdownMenuContent>
                    </DropdownMenu>
                </div>
                <h3 className="text-card-title truncate">{workflow.name}</h3>
                <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">
                    {workflow.description || 'No description'}
                </p>
                <div className="mt-3 flex items-center justify-between">
                    <p className="text-xs text-muted-foreground">
                        Updated {formatDistanceToNow(parseISO(workflow.updated_at), { addSuffix: true })}
                    </p>
                    <div className="flex items-center gap-1.5">
                        {isLocal ? (
                            <Badge variant="outline" className="text-[10px] py-0 h-5 text-muted-foreground">
                                Local
                            </Badge>
                        ) : activeVersion ? (
                            <Badge variant="outline" className="text-[10px] py-0 h-5 gap-0.5">
                                v{activeVersion.version_tag || activeVersion.version_number}
                            </Badge>
                        ) : null}
                    </div>
                </div>
            </div>
        </Link>
    );
}

export default function WorkflowsListPage() {
    const router = useRouter();
    const { data: workflows, isLoading, error } = useWorkflows();
    const createWorkflowMutation = useCreateWorkflow();
    const deleteWorkflowMutation = useDeleteWorkflow();
    const resetWorkflowStore = useWorkflowStore((state: { reset: () => void }) => state.reset);
    const [isCreating, setIsCreating] = useState(false);
    const [creatingTemplateId, setCreatingTemplateId] = useState<string | null>(null);
    const [showFallback, setShowFallback] = useState(false);
    const [isTemplateModalOpen, setIsTemplateModalOpen] = useState(false);
    const [modalExpandedCategory, setModalExpandedCategory] = useState<string | null>('Getting Started');
    const [deleteWorkflowId, setDeleteWorkflowId] = useState<string | null>(null);

    useEffect(() => {
        const timer = setTimeout(() => setShowFallback(true), 1500);
        return () => clearTimeout(timer);
    }, []);

    const actuallyLoading = isLoading && !error && !showFallback;

    const handleCreateWorkflow = () => {
        if (isCreating) return;
        setIsCreating(true);

        // Reset store BEFORE creating workflow to prevent data pollution
        resetWorkflowStore();

        createWorkflowMutation.mutate(
            { spec: { nodes: [], edges: [] }, name: 'Untitled Workflow' },
            {
                onSuccess: (newWorkflow) => {
                    router.push(`/workflows/${newWorkflow.id}`);
                },
                onError: () => {
                    setIsCreating(false);
                    toast.error('Error creating workflow');
                },
            }
        );
    };

    const handleCreateFromTemplate = (template: WorkflowTemplate) => {
        if (creatingTemplateId) return;
        setCreatingTemplateId(template.id);

        // Reset store BEFORE creating workflow to prevent data pollution
        resetWorkflowStore();

        createWorkflowMutation.mutate(
            { spec: { nodes: template.nodes as WorkflowNode[], edges: template.edges as WorkflowEdge[] }, name: template.name },
            {
                onSuccess: (newWorkflow) => {
                    router.push(`/workflows/${newWorkflow.id}`);
                },
                onError: () => {
                    setCreatingTemplateId(null);
                    toast.error('Error creating workflow');
                },
            }
        );
    };

    const handleDeleteWorkflow = (id: string, e: React.MouseEvent) => {
        e.preventDefault();
        e.stopPropagation();
        setDeleteWorkflowId(id);
    };

    const confirmDeleteWorkflow = async () => {
        if (!deleteWorkflowId) return;
        try {
            await deleteWorkflowMutation.mutateAsync(deleteWorkflowId);
            toast('Workflow deleted');
        } catch {
            toast.error('Error deleting workflow');
        }
    };

    const toggleModalCategory = (category: string) => {
        setModalExpandedCategory(modalExpandedCategory === category ? null : category);
    };

    const handleTemplateSelect = (template: WorkflowTemplate) => {
        setIsTemplateModalOpen(false);
        handleCreateFromTemplate(template);
    };

    return (
        <div className="p-6 lg:p-8 max-w-7xl mx-auto">
            {/* Header */}
            <div className="flex items-center justify-between mb-8">
                <div>
                    <h1 className="text-page-title">Workflows</h1>
                    <p className="text-page-description mt-1">
                        Manage and execute AI agent workflows
                    </p>
                </div>
                <div className="flex items-center gap-3">
                    <button
                        onClick={() => setIsTemplateModalOpen(true)}
                        className="flex items-center gap-2 px-4 py-2 border border-border rounded-lg text-sm font-medium hover:bg-muted/50 transition-colors"
                    >
                        <SquaresFour className="h-4 w-4" />
                        Browse Templates
                    </button>
                    <button
                        onClick={handleCreateWorkflow}
                        disabled={isCreating}
                        className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
                    >
                        <Plus className="h-4 w-4" />
                        {isCreating ? 'Creating...' : 'New Workflow'}
                    </button>
                </div>
            </div>

            {actuallyLoading && (
                <div className="flex items-center justify-center py-24">
                    <div className="w-5 h-5 border-2 border-muted-foreground/20 border-t-muted-foreground rounded-full animate-spin" />
                </div>
            )}

            {!actuallyLoading && (
                <>
                    {/* Existing Workflows */}
                    {workflows && workflows.length > 0 && (
                        <section>
                            <div className="flex items-center gap-2 mb-4">
                                <h2 className="text-overline">Your Workflows</h2>
                                <span className="text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
                                    {workflows.length}
                                </span>
                            </div>
                            <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
                                {workflows.map((workflow) => (
                                    <WorkflowCard
                                        key={workflow.id}
                                        workflow={workflow}
                                        onDelete={handleDeleteWorkflow}
                                    />
                                ))}
                            </div>
                        </section>
                    )}

                    {(!workflows || workflows.length === 0) && (
                        <div className="mt-8 border border-dashed border-border rounded-xl p-12">
                            <div className="max-w-md mx-auto text-center">
                                <div className="w-16 h-16 rounded-2xl bg-muted flex items-center justify-center mx-auto mb-6">
                                    <TreeStructure className="h-8 w-8 text-muted-foreground" weight="regular" />
                                </div>
                                <h3 className="text-lg font-semibold mb-2">No workflows yet</h3>
                                <p className="text-sm text-muted-foreground mb-8">
                                    Create your first workflow from scratch or start with a template to get up and running quickly.
                                </p>
                                <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
                                    <button
                                        onClick={() => setIsTemplateModalOpen(true)}
                                        className="flex items-center gap-2 px-5 py-2.5 border border-border rounded-lg text-sm font-medium hover:bg-muted/50 transition-colors w-full sm:w-auto justify-center"
                                    >
                                        <SquaresFour className="h-4 w-4" />
                                        Choose Template
                                    </button>
                                    <button
                                        onClick={handleCreateWorkflow}
                                        disabled={isCreating}
                                        className="flex items-center gap-2 px-5 py-2.5 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50 w-full sm:w-auto justify-center"
                                    >
                                        <FileText className="h-4 w-4" weight="regular" />
                                        {isCreating ? 'Creating...' : 'Create from Scratch'}
                                    </button>
                                </div>
                            </div>
                        </div>
                    )}
                </>
            )}

            {/* Template Selection Modal */}
            <Dialog open={isTemplateModalOpen} onOpenChange={setIsTemplateModalOpen}>
                <DialogContent className="max-w-4xl max-h-[85vh] overflow-hidden flex flex-col">
                    <DialogHeader>
                        <DialogTitle>Choose a Template</DialogTitle>
                        <DialogDescription>
                            Select a template to create a new workflow. You can customize it after creation.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="flex-1 overflow-y-auto pr-2 -mr-2 space-y-2 mt-4">
                        {Object.entries(TEMPLATE_CATEGORIES).map(([category, templateIds]) => {
                            const CategoryIcon = CATEGORY_ICONS[category];
                            const isExpanded = modalExpandedCategory === category;
                            const templates = templateIds
                                .map(id => WORKFLOW_TEMPLATES[id])
                                .filter(Boolean);

                            return (
                                <div key={category} className="border border-border rounded-lg overflow-hidden">
                                    <button
                                        onClick={() => toggleModalCategory(category)}
                                        className="w-full flex items-center gap-3 p-3 hover:bg-muted/50 transition-colors"
                                    >
                                        <div className="w-8 h-8 rounded-lg bg-muted flex items-center justify-center">
                                            <CategoryIcon className="h-4 w-4 text-muted-foreground" weight="regular" />
                                        </div>
                                        <div className="flex-1 text-left">
                                            <span className="text-sm font-medium">{category}</span>
                                            <span className="ml-2 text-xs text-muted-foreground">
                                                ({templates.length})
                                            </span>
                                        </div>
                                        <CaretRight
                                            className={`h-4 w-4 text-muted-foreground transition-transform ${isExpanded ? 'rotate-90' : ''}`}
                                            weight="bold"
                                        />
                                    </button>

                                    {isExpanded && (
                                        <div className="p-3 pt-0 grid gap-3 grid-cols-1 sm:grid-cols-2">
                                            {templates.map((template) => {
                                                const Icon = template.icon;
                                                const isCreatingThis = creatingTemplateId === template.id;
                                                const nodeCount = template.nodes.length;
                                                return (
                                                    <button
                                                        key={template.id}
                                                        onClick={() => handleTemplateSelect(template)}
                                                        disabled={isCreatingThis}
                                                        className="p-4 rounded-lg border border-border hover:border-foreground/20 hover:bg-muted/30 transition-colors text-left disabled:opacity-50"
                                                    >
                                                        <div className="flex items-start gap-3">
                                                            <div className="w-10 h-10 rounded-lg bg-muted flex items-center justify-center flex-shrink-0">
                                                                {isCreatingThis ? (
                                                                    <div className="w-4 h-4 border-2 border-muted-foreground/20 border-t-muted-foreground rounded-full animate-spin" />
                                                                ) : (
                                                                    <Icon className="h-5 w-5 text-muted-foreground" weight="regular" />
                                                                )}
                                                            </div>
                                                            <div className="flex-1 min-w-0">
                                                                <h3 className="text-card-title">{template.name}</h3>
                                                                <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                                                                    {template.description}
                                                                </p>
                                                                <p className="text-xs text-muted-foreground/60 mt-2">
                                                                    {nodeCount} nodes
                                                                </p>
                                                            </div>
                                                        </div>
                                                    </button>
                                                );
                                            })}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                    <div className="flex items-center justify-between pt-4 mt-4 border-t border-border">
                        <span className="text-xs text-muted-foreground">
                            {getTemplateCount()} templates available
                        </span>
                        <button
                            onClick={handleCreateWorkflow}
                            disabled={isCreating}
                            className="flex items-center gap-2 px-4 py-2 text-sm font-medium hover:bg-muted/50 rounded-lg transition-colors disabled:opacity-50"
                        >
                            <FileText className="h-4 w-4" weight="regular" />
                            {isCreating ? 'Creating...' : 'Or start from scratch'}
                        </button>
                    </div>
                </DialogContent>
            </Dialog>

            <ConfirmationDialog
                open={!!deleteWorkflowId}
                onOpenChange={(open) => { if (!open) setDeleteWorkflowId(null); }}
                title="Delete Workflow"
                description="This will permanently delete this workflow and all its execution history."
                onConfirm={confirmDeleteWorkflow}
            />
        </div>
    );
}
