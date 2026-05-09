'use client';

import { memo, useState } from 'react';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { publishAgentAsync, unpublishAgentAsync, type CustomAgent } from '@/lib/api/agents';
import { toast } from 'sonner';
import {
    Globe,
    Users,
    Tag,
    X,
    CircleNotch,
} from '@phosphor-icons/react';
import { useQueryClient } from '@tanstack/react-query';

interface PublishAgentDialogProps {
    agent: CustomAgent | null;
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onPublished?: () => void;
}

const COMMON_TAGS = ['SOC2', 'GDPR', 'HIPAA', 'PCI-DSS', 'SOX', 'CCPA', 'ISO-27001'];

const PublishAgentDialog = memo(function PublishAgentDialog({
    agent,
    open,
    onOpenChange,
    onPublished,
}: PublishAgentDialogProps) {
    const queryClient = useQueryClient();
    const [step, setStep] = useState<1 | 2>(1);
    const [visibility, setVisibility] = useState<'team' | 'tenant'>('tenant');
    const [category, setCategory] = useState(agent?.category || '');
    const [tagInput, setTagInput] = useState('');
    const [complianceTags, setComplianceTags] = useState<string[]>(agent?.complianceTags || []);
    const [isPublishing, setIsPublishing] = useState(false);

    if (!agent) return null;

    const isAlreadyPublished = agent.visibility !== 'private' && agent.visibility !== undefined;

    const handleAddTag = (tag: string) => {
        const trimmed = tag.trim().toUpperCase();
        if (trimmed && !complianceTags.includes(trimmed)) {
            setComplianceTags([...complianceTags, trimmed]);
        }
        setTagInput('');
    };

    const handleRemoveTag = (tag: string) => {
        setComplianceTags(complianceTags.filter(t => t !== tag));
    };

    const handlePublish = async () => {
        setIsPublishing(true);
        try {
            const result = await publishAgentAsync(agent.id, visibility, category || undefined, complianceTags.length > 0 ? complianceTags : undefined);
            if (result) {
                await queryClient.invalidateQueries({ queryKey: ['customAgents'] });
                await queryClient.invalidateQueries({ queryKey: ['organizationAgents'] });
                toast('Published', { description: `${agent.name} is now visible to ${visibility === 'tenant' ? 'the organization' : 'your team'}.` });
                onOpenChange(false);
                setStep(1);
                onPublished?.();
            }
        } catch (error) {
            console.error('Failed to publish agent:', error);
            toast.error('Publish failed', { description: error instanceof Error ? error.message : 'Could not publish agent.' });
        } finally {
            setIsPublishing(false);
        }
    };

    const handleUnpublish = async () => {
        setIsPublishing(true);
        try {
            const result = await unpublishAgentAsync(agent.id);
            if (result) {
                await queryClient.invalidateQueries({ queryKey: ['customAgents'] });
                await queryClient.invalidateQueries({ queryKey: ['organizationAgents'] });
                onOpenChange(false);
                onPublished?.();
            }
        } catch (error) {
            console.error('Failed to unpublish agent:', error);
        } finally {
            setIsPublishing(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) setStep(1); }}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>
                        {isAlreadyPublished ? 'Manage Publication' : 'Publish Agent'}
                    </DialogTitle>
                    <DialogDescription>
                        {isAlreadyPublished
                            ? `"${agent.name}" is currently published as ${agent.visibility}.`
                            : 'Share this agent with your team or organization.'}
                    </DialogDescription>
                </DialogHeader>

                {isAlreadyPublished ? (
                    <div className="space-y-4 py-2">
                        <div className="p-3 rounded-lg bg-muted/50 text-sm">
                            <p className="text-muted-foreground">
                                This agent is visible to {agent.visibility === 'tenant' ? 'the entire organization' : 'your team'}.
                                It has been cloned <strong>{agent.cloneCount ?? 0}</strong> times.
                            </p>
                        </div>
                        <DialogFooter>
                            <button
                                onClick={() => onOpenChange(false)}
                                className="px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
                            >
                                Close
                            </button>
                            <button
                                onClick={handleUnpublish}
                                disabled={isPublishing}
                                className="flex items-center gap-2 px-4 py-2 bg-destructive text-destructive-foreground rounded-lg text-sm font-medium hover:bg-destructive/90 transition-colors disabled:opacity-50"
                            >
                                {isPublishing ? (
                                    <CircleNotch className="h-4 w-4 animate-spin" />
                                ) : null}
                                Unpublish
                            </button>
                        </DialogFooter>
                    </div>
                ) : step === 1 ? (
                    <div className="space-y-4 py-2">
                        {/* Visibility selection */}
                        <div className="space-y-2">
                            <Label>Visibility</Label>
                            <div className="grid grid-cols-2 gap-3">
                                <button
                                    onClick={() => setVisibility('team')}
                                    className={`p-3 rounded-lg border text-left transition-colors ${
                                        visibility === 'team'
                                            ? 'border-primary bg-primary/5'
                                            : 'border-border hover:border-foreground/20'
                                    }`}
                                >
                                    <Users className="h-5 w-5 mb-1.5 text-muted-foreground" />
                                    <div className="text-sm font-medium">Team</div>
                                    <div className="text-xs text-muted-foreground mt-0.5">
                                        Visible to your team members
                                    </div>
                                </button>
                                <button
                                    onClick={() => setVisibility('tenant')}
                                    className={`p-3 rounded-lg border text-left transition-colors ${
                                        visibility === 'tenant'
                                            ? 'border-primary bg-primary/5'
                                            : 'border-border hover:border-foreground/20'
                                    }`}
                                >
                                    <Globe className="h-5 w-5 mb-1.5 text-muted-foreground" />
                                    <div className="text-sm font-medium">Organization</div>
                                    <div className="text-xs text-muted-foreground mt-0.5">
                                        Visible to everyone in the org
                                    </div>
                                </button>
                            </div>
                        </div>

                        {/* Category */}
                        <div className="space-y-2">
                            <Label htmlFor="pub-category">Category (optional)</Label>
                            <Input
                                id="pub-category"
                                placeholder="e.g. Analysis, Research, Compliance"
                                value={category}
                                onChange={(e) => setCategory(e.target.value)}
                            />
                        </div>

                        <DialogFooter>
                            <button
                                onClick={() => onOpenChange(false)}
                                className="px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={() => setStep(2)}
                                className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors"
                            >
                                Next
                            </button>
                        </DialogFooter>
                    </div>
                ) : (
                    <div className="space-y-4 py-2">
                        {/* Compliance tags */}
                        <div className="space-y-2">
                            <Label>Compliance Tags (optional)</Label>
                            <div className="flex flex-wrap gap-1.5 mb-2">
                                {complianceTags.map((tag) => (
                                    <Badge key={tag} variant="secondary" className="text-xs py-0 gap-1">
                                        <Tag className="h-3 w-3" />
                                        {tag}
                                        <button
                                            onClick={() => handleRemoveTag(tag)}
                                            className="ml-0.5 hover:text-destructive"
                                        >
                                            <X className="h-3 w-3" />
                                        </button>
                                    </Badge>
                                ))}
                            </div>
                            <div className="flex gap-2">
                                <Input
                                    placeholder="Add tag..."
                                    value={tagInput}
                                    onChange={(e) => setTagInput(e.target.value)}
                                    onKeyDown={(e) => {
                                        if (e.key === 'Enter') {
                                            e.preventDefault();
                                            handleAddTag(tagInput);
                                        }
                                    }}
                                    className="flex-1"
                                />
                            </div>
                            <div className="flex flex-wrap gap-1 mt-2">
                                {COMMON_TAGS.filter(t => !complianceTags.includes(t)).map((tag) => (
                                    <button
                                        key={tag}
                                        onClick={() => handleAddTag(tag)}
                                        className="px-2 py-0.5 text-xs rounded bg-muted text-muted-foreground hover:text-foreground transition-colors"
                                    >
                                        + {tag}
                                    </button>
                                ))}
                            </div>
                        </div>

                        {/* Summary */}
                        <div className="p-3 rounded-lg bg-muted/50 text-sm space-y-1">
                            <div className="flex items-center gap-2">
                                {visibility === 'tenant' ? (
                                    <Globe className="h-4 w-4 text-muted-foreground" />
                                ) : (
                                    <Users className="h-4 w-4 text-muted-foreground" />
                                )}
                                <span>
                                    Publishing to <strong>{visibility === 'tenant' ? 'Organization' : 'Team'}</strong>
                                </span>
                            </div>
                            {category && (
                                <div className="text-xs text-muted-foreground ml-6">
                                    Category: {category}
                                </div>
                            )}
                            {complianceTags.length > 0 && (
                                <div className="text-xs text-muted-foreground ml-6">
                                    Tags: {complianceTags.join(', ')}
                                </div>
                            )}
                        </div>

                        <DialogFooter>
                            <button
                                onClick={() => setStep(1)}
                                className="px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
                            >
                                Back
                            </button>
                            <button
                                onClick={handlePublish}
                                disabled={isPublishing}
                                className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
                            >
                                {isPublishing ? (
                                    <CircleNotch className="h-4 w-4 animate-spin" />
                                ) : (
                                    <Globe className="h-4 w-4" />
                                )}
                                {isPublishing ? 'Publishing...' : 'Publish'}
                            </button>
                        </DialogFooter>
                    </div>
                )}
            </DialogContent>
        </Dialog>
    );
});

export default PublishAgentDialog;
