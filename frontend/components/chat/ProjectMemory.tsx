'use client';

import { useState } from 'react';
import { Brain, Check, DownloadSimple, PencilSimple, Plus, ShieldWarning, Trash, X } from '@phosphor-icons/react';
import { useProjectMemory } from '@/hooks/useProjectMemory';
import type { MemoryBullet, MemoryFile, MemoryFlag, MemoryRowFlags } from '@/lib/api/project-memory';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { Input } from '@/components/ui/input';
import { ConfirmationDialog } from '@/components/ui/confirmation-dialog';

interface ProjectMemoryProps {
    projectId: string;
    /** Project editor or owner. Viewers get a read-only view. */
    canEdit: boolean;
    /** Tenant admin: may erase all memory. */
    canPurge: boolean;
    /** `project.settings.memory.enabled`: gates injection into chats. */
    memoryEnabled: boolean;
    onToggleEnabled: (next: boolean) => Promise<void>;
}

const FLAG_LABEL: Record<string, string> = {
    imperative: 'Instruction',
    url: 'URL',
    email: 'Email',
    tool_name: 'Tool name',
};

function flagsOf(flags: MemoryRowFlags | null): MemoryFlag[] {
    return Array.isArray(flags?.instruction_flags) ? (flags!.instruction_flags as MemoryFlag[]) : [];
}

function provenance(kind: string, conversationId: string | null, runId?: string | null): string {
    const parts = [`Learned from ${kind === 'user' ? 'a user edit' : kind}`];
    if (conversationId) parts.push(`conversation ${conversationId}`);
    if (runId) parts.push(`run ${runId}`);
    return parts.join(', ');
}

function Flags({ flags, tainted }: { flags: MemoryRowFlags | null; tainted: boolean }) {
    const list = flagsOf(flags);
    if (!tainted && list.length === 0) return null;
    return (
        <div className="mt-2 space-y-1.5">
            {tainted && (
                <span className="inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-900 dark:bg-amber-900/30 dark:text-amber-200">
                    <ShieldWarning className="h-3 w-3" aria-hidden />
                    Tainted source: written after untrusted content was read
                </span>
            )}
            {list.length > 0 && (
                <div>
                    <p className="text-[11px] font-medium text-amber-700 dark:text-amber-300">
                        Looks like an instruction to the assistant. Review before approving.
                    </p>
                    <ul className="mt-1 flex flex-wrap gap-1">
                        {list.map((f, i) => (
                            <li key={`${f.kind}-${i}`} className="rounded border border-amber-300/60 px-1.5 py-0.5 text-[11px]">
                                <span className="text-muted-foreground">{FLAG_LABEL[f.kind] ?? f.kind}: </span>
                                <code>{f.match}</code>
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
}

type Editing = { path: string; content: string; version?: number; isNew: boolean };

export function ProjectMemory({ projectId, canEdit, canPurge, memoryEnabled, onToggleEnabled }: ProjectMemoryProps) {
    const mem = useProjectMemory(projectId);
    const [editing, setEditing] = useState<Editing | null>(null);
    const [confirmPurge, setConfirmPurge] = useState(false);
    const [toggleError, setToggleError] = useState<string | null>(null);

    const writable = canEdit && !mem.readOnly;
    const data = mem.data;
    const pendingFiles = data?.files.filter((f) => f.status === 'pending') ?? [];
    const pendingBullets = data?.bullets.filter((b) => b.status === 'pending') ?? [];
    const activeFiles = data?.files.filter((f) => f.status !== 'pending') ?? [];
    const activeBullets = data?.bullets.filter((b) => b.status !== 'pending') ?? [];
    const hasPending = pendingFiles.length + pendingBullets.length > 0;
    const isEmpty = data !== null && data.files.length === 0 && data.bullets.length === 0;

    const handleToggle = async (next: boolean) => {
        setToggleError(null);
        try {
            await onToggleEnabled(next);
        } catch (e) {
            setToggleError(e instanceof Error ? e.message : 'Could not change the setting.');
        }
    };

    const handleSave = async () => {
        if (!editing || !editing.path.trim()) return;
        const ok = await mem.saveFile(editing.path.trim(), editing.content, editing.version);
        if (ok) setEditing(null);
    };

    const handleExport = async () => {
        const out = await mem.exportAll();
        if (!out) return;
        const url = URL.createObjectURL(new Blob([JSON.stringify(out, null, 2)], { type: 'application/json' }));
        const a = document.createElement('a');
        a.href = url;
        a.download = `project-memory-${projectId}.json`;
        a.click();
        URL.revokeObjectURL(url);
    };

    const startEdit = (f: MemoryFile) => setEditing({ path: f.path, content: f.content, version: f.version, isNew: false });

    return (
        <div className="mx-auto w-full max-w-3xl space-y-8 px-8 py-8">
            <header className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                    <Brain className="h-5 w-5 text-muted-foreground" aria-hidden />
                    <h2 className="text-base font-semibold">Project memory</h2>
                </div>
                <div className="flex flex-wrap items-center gap-3">
                    <label className="flex items-center gap-2 text-sm">
                        <Switch
                            aria-label="Enable project memory"
                            checked={memoryEnabled}
                            onCheckedChange={handleToggle}
                            disabled={!writable}
                        />
                        <span>{memoryEnabled ? 'On' : 'Off'}</span>
                    </label>
                    <Button variant="outline" size="sm" onClick={handleExport}>
                        <DownloadSimple className="mr-1 h-4 w-4" aria-hidden />
                        Export
                    </Button>
                    {canPurge && (
                        <Button variant="outline" size="sm" onClick={() => setConfirmPurge(true)}>
                            <Trash className="mr-1 h-4 w-4" aria-hidden />
                            Delete all memory
                        </Button>
                    )}
                </div>
            </header>

            {!memoryEnabled && (
                <p className="text-sm text-muted-foreground">
                    Memory is off for this project. Nothing below is shared with the assistant until you turn it on.
                </p>
            )}
            {!writable && (
                <p className="rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground">
                    Read-only: you can view what the assistant remembers but not change it.
                </p>
            )}

            {(mem.error || toggleError) && (
                <div role="alert" className="flex items-center justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm">
                    <span>{mem.error?.message ?? toggleError}</span>
                    {mem.error && mem.error.kind !== 'forbidden' && (
                        <Button variant="outline" size="sm" onClick={() => void mem.reload()}>
                            {mem.error.kind === 'conflict' ? 'Reload' : 'Retry'}
                        </Button>
                    )}
                </div>
            )}

            {mem.loading && !data && <p className="text-sm text-muted-foreground">Loading memory...</p>}
            {isEmpty && !mem.loading && (
                <p className="text-sm text-muted-foreground">
                    Nothing remembered yet. The assistant adds notes here as it learns how you work, and you can add your own.
                </p>
            )}

            {hasPending && (
                <section aria-labelledby="mem-pending" className="space-y-3 rounded-lg border border-amber-300/60 p-4">
                    <h3 id="mem-pending" className="text-sm font-semibold">
                        Pending review ({pendingFiles.length + pendingBullets.length})
                    </h3>
                    <p className="text-xs text-muted-foreground">
                        These are not shared with the assistant until you approve them.
                    </p>
                    <ul className="space-y-3">
                        {pendingFiles.map((f) => (
                            <li key={f.id} className="rounded-lg border border-border p-3">
                                <p className="text-xs font-medium">{f.path}</p>
                                <pre className="mt-1 whitespace-pre-wrap text-sm">{f.content}</pre>
                                <p className="mt-1 text-xs text-muted-foreground">
                                    {provenance(f.source_kind, f.source_conversation_id)}
                                </p>
                                <Flags flags={f.flags} tainted={!!f.flags?.tainted} />
                                {writable && (
                                    <DecisionButtons
                                        label={f.path}
                                        busy={mem.busy}
                                        onApprove={() => mem.approve(f.id)}
                                        onReject={() => mem.reject(f.id)}
                                    />
                                )}
                            </li>
                        ))}
                        {pendingBullets.map((b) => (
                            <li key={b.id} className="rounded-lg border border-border p-3">
                                <p className="text-sm">{b.text}</p>
                                <p className="mt-1 text-xs text-muted-foreground">
                                    {provenance(b.source_kind, b.source_conversation_id, b.source_run_id)}
                                </p>
                                <Flags flags={b.flags} tainted={!!b.flags?.tainted} />
                                {writable && (
                                    <DecisionButtons
                                        label={b.text}
                                        busy={mem.busy}
                                        onApprove={() => mem.approve(b.id)}
                                        onReject={() => mem.reject(b.id)}
                                    />
                                )}
                            </li>
                        ))}
                    </ul>
                </section>
            )}

            {data && (
                <section aria-labelledby="mem-files" className="space-y-3">
                    <div className="flex items-center justify-between">
                        <h3 id="mem-files" className="text-sm font-semibold">
                            Files ({activeFiles.length})
                        </h3>
                        {writable && (
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => setEditing({ path: '', content: '', isNew: true })}
                            >
                                <Plus className="mr-1 h-4 w-4" aria-hidden />
                                New file
                            </Button>
                        )}
                    </div>

                    {editing && (
                        <div className="space-y-2 rounded-lg border border-border p-3">
                            {editing.isNew && (
                                <div>
                                    <label htmlFor="mem-new-path" className="text-xs font-medium">
                                        File path
                                    </label>
                                    <Input
                                        id="mem-new-path"
                                        value={editing.path}
                                        placeholder="notes.md"
                                        onChange={(e) => setEditing({ ...editing, path: e.target.value })}
                                    />
                                </div>
                            )}
                            <label htmlFor="mem-editor" className="sr-only">
                                Content of {editing.path || 'new file'}
                            </label>
                            <Textarea
                                id="mem-editor"
                                rows={8}
                                value={editing.content}
                                onChange={(e) => setEditing({ ...editing, content: e.target.value })}
                            />
                            <div className="flex gap-2">
                                <Button size="sm" onClick={handleSave} disabled={mem.busy || !editing.path.trim()}>
                                    Save
                                </Button>
                                <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
                                    Cancel
                                </Button>
                            </div>
                        </div>
                    )}

                    <ul className="space-y-2">
                        {activeFiles.map((f) => (
                            <li key={f.id} className="rounded-lg border border-border p-3">
                                <div className="flex items-center justify-between gap-2">
                                    <span className="text-sm font-medium">{f.path}</span>
                                    {writable && (
                                        <span className="flex gap-1">
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                aria-label={`Edit ${f.path}`}
                                                onClick={() => startEdit(f)}
                                            >
                                                <PencilSimple className="h-4 w-4" aria-hidden />
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                aria-label={`Delete ${f.path}`}
                                                disabled={mem.busy}
                                                onClick={() => void mem.deleteFile(f.path, f.version)}
                                            >
                                                <Trash className="h-4 w-4" aria-hidden />
                                            </Button>
                                        </span>
                                    )}
                                </div>
                                <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-sm text-muted-foreground">
                                    {f.content}
                                </pre>
                            </li>
                        ))}
                    </ul>
                </section>
            )}

            {data && activeBullets.length > 0 && (
                <section aria-labelledby="mem-playbook" className="space-y-3">
                    <h3 id="mem-playbook" className="text-sm font-semibold">
                        Playbook ({activeBullets.length})
                    </h3>
                    <ul className="space-y-2">
                        {activeBullets.map((b: MemoryBullet) => (
                            <li key={b.id} className="flex items-start justify-between gap-3 rounded-lg border border-border p-3">
                                <div>
                                    <p className="text-sm">{b.text}</p>
                                    <p className="mt-1 text-xs text-muted-foreground">
                                        {b.section}
                                        {' · '}
                                        <span aria-label={`Helpful ${b.helpful}`}>+{b.helpful}</span>
                                        {' / '}
                                        <span aria-label={`Harmful ${b.harmful}`}>-{b.harmful}</span>
                                    </p>
                                </div>
                                {writable && (
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        aria-label={`Delete bullet: ${b.text}`}
                                        disabled={mem.busy}
                                        onClick={() => void mem.deleteBullet(b.id)}
                                    >
                                        <Trash className="h-4 w-4" aria-hidden />
                                    </Button>
                                )}
                            </li>
                        ))}
                    </ul>
                </section>
            )}

            <ConfirmationDialog
                open={confirmPurge}
                onOpenChange={setConfirmPurge}
                title="Delete all project memory?"
                description="This permanently erases every memory file and playbook bullet for this project, including pending items. It cannot be undone."
                confirmLabel="Delete everything"
                onConfirm={async () => {
                    await mem.purge();
                }}
            />
        </div>
    );
}

function DecisionButtons({
    label,
    busy,
    onApprove,
    onReject,
}: {
    label: string;
    busy: boolean;
    onApprove: () => unknown;
    onReject: () => unknown;
}) {
    const short = label.length > 40 ? `${label.slice(0, 40)}...` : label;
    return (
        <div className="mt-3 flex gap-2">
            <Button size="sm" disabled={busy} onClick={() => void onApprove()} aria-label={`Approve: ${short}`}>
                <Check className="mr-1 h-4 w-4" aria-hidden />
                Approve
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => void onReject()} aria-label={`Reject: ${short}`}>
                <X className="mr-1 h-4 w-4" aria-hidden />
                Reject
            </Button>
        </div>
    );
}
