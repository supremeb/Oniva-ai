'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import {
    FolderSimple,
    ListDashes,
    Plus,
    Trash,
    ArrowRight,
    CaretDown,
    File as FileIcon,
    SpinnerGap,
    Users,
    PencilSimple,
    UploadSimple,
    FilePdf,
    FileText,
    FileCode,
    FileCsv,
    FileImage,
} from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import { SetInstructionsModal } from './SetInstructionsModal';
import { ProjectMembersPanel } from './ProjectMembersPanel';
import { ProjectMemory } from './ProjectMemory';
import { useFeature } from '@/lib/hooks/useFeature';
import { useAvailableModels } from '@/hooks/useLLMProviders';
import { useAuthStore } from '@/lib/stores/auth';
import { useProjectsStore } from '@/lib/stores/projects';
import { useChatSessionsStore } from '@/lib/stores/chat-sessions';
import { useEngineNav } from '@/lib/stores/engine-nav';
import {
    uploadProjectFile,
    fetchProjectFiles,
    deleteProjectFile,
    fetchProjectSessions,
    updateProject,
} from '@/lib/api/projects';
import {
    ResizablePanelGroup,
    ResizablePanel,
    ResizableHandle,
} from '@/components/ui/resizable';
import { ScrollArea } from '@/components/ui/scroll-area';
import type { Project, ProjectFile } from '@/lib/models';

interface ProjectViewProps {
    project: Project;
}

/** Map file extension → icon component */
function getFileIcon(filename: string) {
    const ext = filename.split('.').pop()?.toLowerCase();
    switch (ext) {
        case 'pdf':
            return FilePdf;
        case 'csv':
        case 'tsv':
            return FileCsv;
        case 'png':
        case 'jpg':
        case 'jpeg':
        case 'gif':
        case 'webp':
        case 'svg':
            return FileImage;
        case 'py':
        case 'js':
        case 'ts':
        case 'tsx':
        case 'jsx':
        case 'json':
        case 'yaml':
        case 'yml':
            return FileCode;
        case 'txt':
        case 'md':
        case 'rst':
            return FileText;
        default:
            return FileIcon;
    }
}

/** Human-readable file extension badge */
function getFileExtBadge(filename: string): string {
    const ext = filename.split('.').pop()?.toUpperCase();
    return ext || 'FILE';
}

/** Format bytes to human-readable */
function formatFileSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ProjectView({ project }: ProjectViewProps) {
    const router = useRouter();
    const [isInstructionsModalOpen, setIsInstructionsModalOpen] = useState(false);
    const [isMembersPanelOpen, setIsMembersPanelOpen] = useState(false);
    const [isDragging, setIsDragging] = useState(false);
    const [files, setFiles] = useState<ProjectFile[]>([]);
    const [sessions, setSessions] = useState<Record<string, unknown>[]>([]);
    const [uploading, setUploading] = useState(false);
    const [chatInput, setChatInput] = useState('');
    const [tab, setTab] = useState<'overview' | 'memory'>('overview');
    const memoryFeature = useFeature('ENABLE_PROJECT_MEMORY');
    const [memoryEnabled, setMemoryEnabled] = useState(
        (project.settings?.memory as { enabled?: boolean } | undefined)?.enabled === true
    );
    const rowVersionRef = useRef(project.row_version);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const currentUserId = useAuthStore((s) => s.user?.id ?? '');
    const isTenantAdmin = useAuthStore((s) => s.user?.role === 'owner' || s.user?.role === 'admin');
    const { updateProject: storeUpdateProject } = useProjectsStore();
    const { createSession, setCurrentSession, selectedModel, setSelectedModel, setPendingInput } =
        useChatSessionsStore();
    const engineNav = useEngineNav();
    const { models: availableModels } = useAvailableModels();

    // Load files and sessions
    useEffect(() => {
        fetchProjectFiles(project.id)
            .then((res) => setFiles(res.files as unknown as ProjectFile[]))
            .catch(() => {});
        fetchProjectSessions(project.id)
            .then((res) => setSessions(res.sessions))
            .catch(() => {});
    }, [project.id]);

    // Poll for file status updates while any file is still processing
    useEffect(() => {
        const hasPending = files.some(
            (f) => f.status === 'uploading' || f.status === 'processing'
        );
        if (!hasPending) return;

        const interval = setInterval(() => {
            fetchProjectFiles(project.id)
                .then((res) => setFiles(res.files as unknown as ProjectFile[]))
                .catch(() => {});
        }, 3000);
        return () => clearInterval(interval);
    }, [files, project.id]);

    // Auto-resize textarea
    useEffect(() => {
        const el = textareaRef.current;
        if (el) {
            el.style.height = 'auto';
            el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
        }
    }, [chatInput]);

    const handleSaveInstructions = async (instructions: string) => {
        await storeUpdateProject(project.id, {
            instructions,
            row_version: project.row_version,
        });
    };

    const handleUploadFiles = async (fileList: File[]) => {
        setUploading(true);
        try {
            for (const file of fileList) {
                const doc = await uploadProjectFile(project.id, file);
                setFiles((prev) => [doc, ...prev]);
            }
        } finally {
            setUploading(false);
        }
    };

    const handleDragOver = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(true);
    }, []);

    const handleDragLeave = useCallback(() => {
        setIsDragging(false);
    }, []);

    const handleDrop = useCallback(
        (e: React.DragEvent) => {
            e.preventDefault();
            setIsDragging(false);
            handleUploadFiles(Array.from(e.dataTransfer.files));
        },
        [project.id]
    );

    const handleFileSelect = useCallback(
        (e: React.ChangeEvent<HTMLInputElement>) => {
            handleUploadFiles(Array.from(e.target.files || []));
            if (fileInputRef.current) fileInputRef.current.value = '';
        },
        [project.id]
    );

    const handleDeleteFile = async (fileId: string) => {
        await deleteProjectFile(project.id, fileId);
        setFiles((prev) => prev.filter((f) => f.id !== fileId));
    };

    const handleNewChat = () => {
        if (chatInput.trim()) {
            setPendingInput(chatInput.trim());
        }
        const sessionId = createSession(null, project.id);
        setCurrentSession(sessionId);
    };

    const handleChatSubmit = () => {
        if (!chatInput.trim()) return;
        handleNewChat();
    };

    // Engine conversations reopen on this project page with ?c=; legacy sessions use the store.
    const handleSelectSession = (session: Record<string, unknown>) => {
        if (session.source === 'engine') {
            setCurrentSession(null);
            engineNav.open(session.id as string);
            router.push(`/chat/projects/${project.id}?c=${encodeURIComponent(session.id as string)}`);
            return;
        }
        setCurrentSession(session.id as string);
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleChatSubmit();
        }
    };

    const handleModelChange = async (model: string) => {
        setSelectedModel(model || null);
        await updateProject(project.id, {
            default_model: model || undefined,
            row_version: project.row_version,
        }).catch(() => {});
    };

    const canEdit = project.user_role === 'owner' || project.user_role === 'editor';

    // Writes `settings.memory.enabled` (gates injection); a 409 surfaces to the Memory tab's error line.
    const handleToggleMemory = async (next: boolean) => {
        const memory = (project.settings?.memory as Record<string, unknown> | undefined) ?? {};
        const updated = await updateProject(project.id, {
            settings: { ...project.settings, memory: { ...memory, enabled: next } },
            row_version: rowVersionRef.current,
        });
        rowVersionRef.current = updated.row_version;
        setMemoryEnabled(next);
    };

    return (
        <div className="h-full flex flex-col">
            {/* ── Project Header ── */}
            <div className="px-8 pt-6 pb-4">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <FolderSimple className="h-7 w-7 text-muted-foreground" weight="duotone" />
                        <div>
                            <h1 className="text-xl font-semibold">{project.name}</h1>
                            {project.description && (
                                <p className="text-sm text-muted-foreground mt-0.5">
                                    {project.description}
                                </p>
                            )}
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        {project.member_count > 1 && (
                            <span className="flex items-center gap-1 text-xs text-muted-foreground bg-muted px-2 py-1 rounded-full">
                                <Users className="h-3 w-3" />
                                {project.member_count}
                            </span>
                        )}
                        {canEdit && (
                            <button
                                onClick={() => setIsMembersPanelOpen(true)}
                                className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground bg-muted hover:bg-muted/80 px-2.5 py-1 rounded-full transition-colors"
                            >
                                <Users className="h-3.5 w-3.5" />
                                Share
                            </button>
                        )}
                        <span className="text-xs text-muted-foreground bg-muted px-2 py-1 rounded-full capitalize">
                            {project.user_role}
                        </span>
                    </div>
                </div>
            </div>

            {memoryFeature && (
                <div role="tablist" aria-label="Project sections" className="flex gap-1 px-8 border-b border-border">
                    {(['overview', 'memory'] as const).map((t) => (
                        <button
                            key={t}
                            role="tab"
                            id={`project-tab-${t}`}
                            aria-selected={tab === t}
                            aria-controls={`project-panel-${t}`}
                            onClick={() => setTab(t)}
                            className={cn(
                                'px-3 py-2 text-sm capitalize border-b-2 -mb-px transition-colors',
                                tab === t
                                    ? 'border-primary text-foreground'
                                    : 'border-transparent text-muted-foreground hover:text-foreground'
                            )}
                        >
                            {t}
                        </button>
                    ))}
                </div>
            )}

            {memoryFeature && tab === 'memory' && (
                <div id="project-panel-memory" role="tabpanel" aria-labelledby="project-tab-memory" className="flex-1 min-h-0">
                    <ScrollArea className="h-full">
                        <ProjectMemory
                            projectId={project.id}
                            canEdit={canEdit}
                            canPurge={isTenantAdmin}
                            memoryEnabled={memoryEnabled}
                            onToggleEnabled={handleToggleMemory}
                        />
                    </ScrollArea>
                </div>
            )}

            {/* ── Two-Column Layout ── */}
            <div
                id="project-panel-overview"
                className={cn('flex-1 min-h-0 border-t border-border', memoryFeature && tab === 'memory' && 'hidden')}
            >
                <ResizablePanelGroup direction="horizontal">
                    {/* ── LEFT PANEL: Chat + Recent Chats ── */}
                    <ResizablePanel defaultSize={65} minSize={40}>
                        <ScrollArea className="h-full">
                            <div className="flex flex-col items-center justify-start px-8 py-10">
                                {/* Chat input area */}
                                <div className="w-full max-w-[640px]">
                                    <h2 className="text-lg font-medium text-center mb-6 text-muted-foreground">
                                        How can I help you today?
                                    </h2>

                                    {/* Chat input box — styled like WelcomeInput */}
                                    <div
                                        className={cn(
                                            'relative bg-white dark:bg-neutral-900 rounded-2xl',
                                            'shadow-[0_2px_12px_rgba(0,0,0,0.08)]',
                                            'border border-neutral-200/60 dark:border-neutral-700/60',
                                            'transition-all duration-200',
                                            'focus-within:shadow-[0_4px_20px_rgba(0,0,0,0.12)]',
                                            'focus-within:border-neutral-300 dark:focus-within:border-neutral-600'
                                        )}
                                    >
                                        <textarea
                                            ref={textareaRef}
                                            value={chatInput}
                                            onChange={(e) => setChatInput(e.target.value)}
                                            onKeyDown={handleKeyDown}
                                            placeholder="Ask about your project files, get analysis..."
                                            rows={1}
                                            className={cn(
                                                'w-full px-5 pt-4 pb-14 bg-transparent resize-none',
                                                'min-h-[60px] max-h-[200px]',
                                                'text-[15px] text-neutral-900 dark:text-neutral-100 placeholder:text-neutral-400',
                                                'focus:outline-none focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0',
                                                'rounded-2xl'
                                            )}
                                        />

                                        {/* Bottom toolbar */}
                                        <div className="absolute bottom-0 left-0 right-0 px-3 pb-3 flex items-center justify-between">
                                            {/* Model selector */}
                                            <div className="relative">
                                                <select
                                                    value={project.default_model ?? selectedModel ?? ''}
                                                    onChange={(e) => handleModelChange(e.target.value)}
                                                    className="appearance-none bg-transparent text-xs text-muted-foreground pr-5 cursor-pointer focus:outline-none"
                                                >
                                                    <option value="">Provider Default</option>
                                                    {availableModels.map((model) => (
                                                        <option key={model.id} value={model.id}>
                                                            {model.name.replace(/ \(.*\)/, '')}
                                                        </option>
                                                    ))}
                                                </select>
                                                <CaretDown className="absolute right-0 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground pointer-events-none" />
                                            </div>

                                            {/* Send button */}
                                            <button
                                                onClick={handleChatSubmit}
                                                disabled={!chatInput.trim()}
                                                className={cn(
                                                    'h-8 w-8 rounded-full flex items-center justify-center transition-colors',
                                                    chatInput.trim()
                                                        ? 'bg-primary text-primary-foreground hover:bg-primary/90'
                                                        : 'bg-muted text-muted-foreground'
                                                )}
                                            >
                                                <ArrowRight className="h-4 w-4" />
                                            </button>
                                        </div>
                                    </div>

                                    {/* Recent Chats */}
                                    <div className="mt-10">
                                        <h3 className="text-sm font-medium text-muted-foreground mb-3">
                                            Recent Chats
                                        </h3>
                                        {sessions.length === 0 ? (
                                            <p className="text-sm text-muted-foreground">
                                                No chats yet. Start a conversation above.
                                            </p>
                                        ) : (
                                            <div className="space-y-1">
                                                {sessions.map((session) => (
                                                    <button
                                                        key={session.id as string}
                                                        onClick={() => handleSelectSession(session)}
                                                        className="w-full text-left px-3 py-2 rounded-lg hover:bg-muted transition-colors text-sm"
                                                    >
                                                        {(session.title as string) || 'Untitled Chat'}
                                                    </button>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            </div>
                        </ScrollArea>
                    </ResizablePanel>

                    <ResizableHandle />

                    {/* ── RIGHT PANEL: Instructions + Files ── */}
                    <ResizablePanel defaultSize={35} minSize={25}>
                        <ScrollArea className="h-full">
                            <div className="px-6 py-6 space-y-8">
                                {/* Instructions Section */}
                                <div>
                                    <div className="flex items-center justify-between mb-2">
                                        <h3 className="text-sm font-semibold">Instructions</h3>
                                        {canEdit && (
                                            <button
                                                onClick={() => setIsInstructionsModalOpen(true)}
                                                className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                                            >
                                                <PencilSimple className="h-3.5 w-3.5" />
                                                Edit
                                            </button>
                                        )}
                                    </div>
                                    {project.instructions ? (
                                        <div className="p-3 rounded-lg bg-muted/50 text-sm whitespace-pre-wrap text-muted-foreground leading-relaxed">
                                            {project.instructions}
                                        </div>
                                    ) : (
                                        <button
                                            onClick={() => canEdit && setIsInstructionsModalOpen(true)}
                                            disabled={!canEdit}
                                            className="w-full p-3 rounded-lg border border-dashed border-border text-sm text-muted-foreground hover:border-muted-foreground/50 hover:text-foreground transition-colors text-center disabled:cursor-default disabled:hover:border-border disabled:hover:text-muted-foreground"
                                        >
                                            Add instructions to tailor responses in this project
                                        </button>
                                    )}
                                </div>

                                {/* Files Section */}
                                <div>
                                    <div className="flex items-center justify-between mb-3">
                                        <h3 className="text-sm font-semibold">
                                            Files
                                            {files.length > 0 && (
                                                <span className="text-muted-foreground font-normal ml-1">
                                                    ({files.length})
                                                </span>
                                            )}
                                        </h3>
                                        {canEdit && (
                                            <button
                                                onClick={() => fileInputRef.current?.click()}
                                                disabled={uploading}
                                                className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
                                            >
                                                {uploading ? (
                                                    <SpinnerGap className="h-3.5 w-3.5 animate-spin" />
                                                ) : (
                                                    <Plus className="h-3.5 w-3.5" />
                                                )}
                                                Add
                                            </button>
                                        )}
                                    </div>
                                    <input
                                        ref={fileInputRef}
                                        type="file"
                                        multiple
                                        className="hidden"
                                        onChange={handleFileSelect}
                                    />

                                    {/* File cards grid + drag-drop zone */}
                                    <div
                                        onDragOver={handleDragOver}
                                        onDragLeave={handleDragLeave}
                                        onDrop={handleDrop}
                                        className={cn(
                                            'rounded-lg transition-colors',
                                            isDragging && 'ring-2 ring-primary bg-primary/5'
                                        )}
                                    >
                                        {files.length === 0 ? (
                                            <button
                                                onClick={() => canEdit && fileInputRef.current?.click()}
                                                disabled={!canEdit}
                                                className="w-full p-6 rounded-lg border-2 border-dashed border-border text-sm text-muted-foreground hover:border-muted-foreground/50 hover:text-foreground transition-colors text-center disabled:cursor-default disabled:hover:border-border disabled:hover:text-muted-foreground"
                                            >
                                                <UploadSimple className="h-6 w-6 mx-auto mb-2 opacity-50" />
                                                Drop files here or click to upload
                                            </button>
                                        ) : (
                                            <div className="grid grid-cols-2 gap-2">
                                                {files.map((file) => {
                                                    const Icon = getFileIcon(file.filename);
                                                    return (
                                                        <div
                                                            key={file.id}
                                                            className="group relative p-3 rounded-lg border border-border bg-muted/30 hover:bg-muted/60 transition-colors"
                                                        >
                                                            {canEdit && (
                                                                <button
                                                                    onClick={() => handleDeleteFile(file.id)}
                                                                    className="absolute top-1.5 right-1.5 text-muted-foreground hover:text-destructive transition-colors opacity-0 group-hover:opacity-100"
                                                                >
                                                                    <Trash className="h-3.5 w-3.5" />
                                                                </button>
                                                            )}
                                                            <Icon className="h-8 w-8 text-muted-foreground mb-2" weight="duotone" />
                                                            <p className="text-xs font-medium truncate" title={file.filename}>
                                                                {file.filename}
                                                            </p>
                                                            <div className="flex items-center gap-1.5 mt-1">
                                                                <span className="text-[10px] font-medium text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                                                                    {getFileExtBadge(file.filename)}
                                                                </span>
                                                                {file.status === 'ready' ? (
                                                                    <span className="text-[10px] text-muted-foreground">
                                                                        {file.chunk_count} chunks
                                                                    </span>
                                                                ) : file.status === 'error' ? (
                                                                    <span className="text-[10px] text-red-500">
                                                                        Error
                                                                    </span>
                                                                ) : (
                                                                    <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                                                                        <SpinnerGap className="h-2.5 w-2.5 animate-spin" />
                                                                        {file.status}
                                                                    </span>
                                                                )}
                                                            </div>
                                                        </div>
                                                    );
                                                })}

                                                {/* Add more button tile */}
                                                {canEdit && (
                                                    <button
                                                        onClick={() => fileInputRef.current?.click()}
                                                        disabled={uploading}
                                                        className="p-3 rounded-lg border border-dashed border-border hover:border-muted-foreground/50 hover:bg-muted/30 transition-colors flex flex-col items-center justify-center text-muted-foreground disabled:opacity-50"
                                                    >
                                                        {uploading ? (
                                                            <SpinnerGap className="h-6 w-6 animate-spin" />
                                                        ) : (
                                                            <>
                                                                <Plus className="h-6 w-6 mb-1" />
                                                                <span className="text-[10px]">Add file</span>
                                                            </>
                                                        )}
                                                    </button>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            </div>
                        </ScrollArea>
                    </ResizablePanel>
                </ResizablePanelGroup>
            </div>

            <SetInstructionsModal
                isOpen={isInstructionsModalOpen}
                onClose={() => setIsInstructionsModalOpen(false)}
                onSave={handleSaveInstructions}
                initialInstructions={project.instructions ?? ''}
            />

            <ProjectMembersPanel
                projectId={project.id}
                userRole={project.user_role}
                currentUserId={currentUserId}
                isOpen={isMembersPanelOpen}
                onClose={() => setIsMembersPanelOpen(false)}
            />
        </div>
    );
}
