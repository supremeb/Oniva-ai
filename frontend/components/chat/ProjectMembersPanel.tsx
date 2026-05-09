'use client';

import { useState, useEffect } from 'react';
import {
    UserPlus,
    X,
    Crown,
    PencilSimple,
    Eye,
    Trash,
    SignOut,
    CaretDown,
} from '@phosphor-icons/react';
import { cn } from '@/lib/utils';
import {
    fetchProjectMembers,
    addProjectMember,
    updateMemberRole,
    removeMember,
} from '@/lib/api/projects';
import type { ProjectMember, ProjectRole } from '@/lib/models';

interface ProjectMembersPanelProps {
    projectId: string;
    userRole: ProjectRole;
    currentUserId: string;
    isOpen: boolean;
    onClose: () => void;
}

const roleIcons: Record<string, typeof Crown> = {
    owner: Crown,
    editor: PencilSimple,
    viewer: Eye,
};

export function ProjectMembersPanel({
    projectId,
    userRole,
    currentUserId,
    isOpen,
    onClose,
}: ProjectMembersPanelProps) {
    const [members, setMembers] = useState<ProjectMember[]>([]);
    const [inviteEmail, setInviteEmail] = useState('');
    const [inviteRole, setInviteRole] = useState<'editor' | 'viewer'>('viewer');
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (isOpen) {
            fetchProjectMembers(projectId)
                .then((res) => setMembers(res.members))
                .catch(() => {});
        }
    }, [projectId, isOpen]);

    const handleInvite = async () => {
        if (!inviteEmail.trim()) return;
        setLoading(true);
        try {
            // Note: In a real implementation, we'd search users by email first
            // For now, the API expects a user_id
            await addProjectMember(projectId, {
                user_id: inviteEmail, // This should be a user ID
                role: inviteRole,
            });
            const res = await fetchProjectMembers(projectId);
            setMembers(res.members);
            setInviteEmail('');
        } catch {
            // Handle error
        } finally {
            setLoading(false);
        }
    };

    const handleRoleChange = async (userId: string, newRole: 'editor' | 'viewer') => {
        try {
            await updateMemberRole(projectId, userId, newRole);
            setMembers((prev) =>
                prev.map((m) =>
                    m.user_id === userId ? { ...m, role: newRole } : m
                )
            );
        } catch {
            // Handle error
        }
    };

    const handleRemove = async (userId: string) => {
        try {
            await removeMember(projectId, userId);
            setMembers((prev) => prev.filter((m) => m.user_id !== userId));
        } catch {
            // Handle error
        }
    };

    const handleLeave = async () => {
        try {
            await removeMember(projectId, currentUserId);
            onClose();
        } catch {
            // Handle error
        }
    };

    if (!isOpen) return null;

    const isOwner = userRole === 'owner';

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
            <div className="bg-background rounded-xl shadow-lg w-full max-w-md mx-4">
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-border">
                    <h2 className="text-lg font-semibold">Project Members</h2>
                    <button
                        onClick={onClose}
                        className="text-muted-foreground hover:text-foreground transition-colors"
                    >
                        <X className="h-5 w-5" />
                    </button>
                </div>

                {/* Invite Section (Owner only) */}
                {isOwner && (
                    <div className="px-6 py-4 border-b border-border">
                        <div className="flex gap-2">
                            <input
                                type="text"
                                value={inviteEmail}
                                onChange={(e) => setInviteEmail(e.target.value)}
                                placeholder="User ID to invite"
                                className="flex-1 px-3 py-2 text-sm border border-border rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
                            />
                            <div className="relative">
                                <select
                                    value={inviteRole}
                                    onChange={(e) =>
                                        setInviteRole(e.target.value as 'editor' | 'viewer')
                                    }
                                    className="appearance-none px-3 py-2 pr-7 text-sm border border-border rounded-lg bg-background focus:outline-none cursor-pointer"
                                >
                                    <option value="viewer">Viewer</option>
                                    <option value="editor">Editor</option>
                                </select>
                                <CaretDown className="absolute right-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground pointer-events-none" />
                            </div>
                            <button
                                onClick={handleInvite}
                                disabled={loading || !inviteEmail.trim()}
                                className="px-3 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors"
                            >
                                <UserPlus className="h-4 w-4" />
                            </button>
                        </div>
                    </div>
                )}

                {/* Members List */}
                <div className="px-6 py-4 max-h-[300px] overflow-y-auto">
                    <div className="space-y-2">
                        {members.map((member) => {
                            const RoleIcon = roleIcons[member.role] || Eye;
                            const isSelf = member.user_id === currentUserId;
                            const isOwnerMember = member.role === 'owner';

                            return (
                                <div
                                    key={member.user_id}
                                    className="flex items-center justify-between py-2 group"
                                >
                                    <div className="flex items-center gap-3 min-w-0">
                                        <div className="h-8 w-8 rounded-full bg-muted flex items-center justify-center text-xs font-medium shrink-0">
                                            {(member.user_name || member.user_email || '?')[0].toUpperCase()}
                                        </div>
                                        <div className="min-w-0">
                                            <div className="text-sm truncate">
                                                {member.user_name || member.user_email}
                                                {isSelf && (
                                                    <span className="text-muted-foreground ml-1">
                                                        (you)
                                                    </span>
                                                )}
                                            </div>
                                            {member.user_email && member.user_name && (
                                                <div className="text-xs text-muted-foreground truncate">
                                                    {member.user_email}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        {isOwner && !isOwnerMember ? (
                                            <>
                                                <select
                                                    value={member.role}
                                                    onChange={(e) =>
                                                        handleRoleChange(
                                                            member.user_id,
                                                            e.target.value as 'editor' | 'viewer'
                                                        )
                                                    }
                                                    className="appearance-none text-xs px-2 py-1 border border-border rounded bg-background cursor-pointer focus:outline-none"
                                                >
                                                    <option value="viewer">Viewer</option>
                                                    <option value="editor">Editor</option>
                                                </select>
                                                <button
                                                    onClick={() => handleRemove(member.user_id)}
                                                    className="text-muted-foreground hover:text-destructive opacity-0 group-hover:opacity-100 transition-all"
                                                >
                                                    <Trash className="h-4 w-4" />
                                                </button>
                                            </>
                                        ) : (
                                            <span className="flex items-center gap-1 text-xs text-muted-foreground capitalize">
                                                <RoleIcon className="h-3 w-3" />
                                                {member.role}
                                            </span>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>

                {/* Leave Project (non-owners) */}
                {!isOwner && (
                    <div className="px-6 py-3 border-t border-border">
                        <button
                            onClick={handleLeave}
                            className="flex items-center gap-2 text-sm text-destructive hover:text-destructive/80 transition-colors"
                        >
                            <SignOut className="h-4 w-4" />
                            Leave Project
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
