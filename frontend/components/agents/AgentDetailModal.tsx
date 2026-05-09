'use client';

import { memo, useState } from 'react';
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { CustomAgent, cloneAgentAsync } from '@/lib/api/agents';
import {
    Database,
    ChartLineUp,
    ShieldCheck,
    BookOpenText,
    Robot,
    Copy,
    ArrowDown,
    Tag,
    User,
    Calendar,
    ChatCircle,
} from '@phosphor-icons/react';
import { useQueryClient } from '@tanstack/react-query';

const ICONS: Record<string, React.ElementType> = {
    'Database': Database,
    'ChartLineUp': ChartLineUp,
    'ShieldCheck': ShieldCheck,
    'BookOpenText': BookOpenText,
    'Robot': Robot,
    'ChatCircle': ChatCircle,
};

interface AgentDetailModalProps {
    agent: CustomAgent | null;
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onCloned?: () => void;
}

const AgentDetailModal = memo(function AgentDetailModal({
    agent,
    open,
    onOpenChange,
    onCloned,
}: AgentDetailModalProps) {
    const [isCloning, setIsCloning] = useState(false);
    const queryClient = useQueryClient();

    if (!agent) return null;

    const IconComponent = ICONS[agent.icon] || Robot;

    const handleClone = async () => {
        setIsCloning(true);
        try {
            const cloned = await cloneAgentAsync(agent.id, `${agent.name} (Copy)`);
            if (cloned) {
                await queryClient.invalidateQueries({ queryKey: ['customAgents'] });
                onOpenChange(false);
                onCloned?.();
            }
        } catch (error) {
            console.error('Failed to clone agent:', error);
        } finally {
            setIsCloning(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-lg">
                <DialogHeader>
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-lg bg-muted flex items-center justify-center">
                            <IconComponent className="h-5 w-5 text-muted-foreground" weight="regular" />
                        </div>
                        <div>
                            <DialogTitle className="text-lg">{agent.name}</DialogTitle>
                            {agent.category && (
                                <span className="text-xs text-muted-foreground">{agent.category}</span>
                            )}
                        </div>
                    </div>
                </DialogHeader>

                <div className="space-y-4 pt-2">
                    {/* Description */}
                    <p className="text-sm text-muted-foreground leading-relaxed">
                        {agent.description || 'No description provided.'}
                    </p>

                    {/* Metadata grid */}
                    <div className="grid grid-cols-2 gap-3">
                        <div className="p-3 rounded-lg bg-muted/50">
                            <div className="text-xs text-muted-foreground mb-1">Model</div>
                            <div className="text-sm font-medium truncate">{agent.model || 'gpt-4o'}</div>
                        </div>
                        <div className="p-3 rounded-lg bg-muted/50">
                            <div className="text-xs text-muted-foreground mb-1">Clones</div>
                            <div className="text-sm font-medium flex items-center gap-1">
                                <ArrowDown className="h-3.5 w-3.5" />
                                {agent.cloneCount ?? 0}
                            </div>
                        </div>
                    </div>

                    {/* Skills */}
                    {agent.skills && agent.skills.length > 0 && (
                        <div>
                            <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
                                Skills ({agent.skills.length})
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                                {agent.skills.map((skill) => (
                                    <Badge key={skill} variant="secondary" className="text-xs py-0">
                                        {skill}
                                    </Badge>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Compliance Tags */}
                    {agent.complianceTags && agent.complianceTags.length > 0 && (
                        <div>
                            <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
                                Compliance
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                                {agent.complianceTags.map((tag) => (
                                    <Badge key={tag} variant="outline" className="text-xs py-0">
                                        <Tag className="h-3 w-3 mr-1" />
                                        {tag}
                                    </Badge>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Published info */}
                    {agent.publishedAt && (
                        <div className="flex items-center gap-4 text-xs text-muted-foreground pt-1 border-t">
                            <span className="flex items-center gap-1">
                                <Calendar className="h-3.5 w-3.5" />
                                Published {new Date(agent.publishedAt).toLocaleDateString()}
                            </span>
                        </div>
                    )}

                    {/* Clone button */}
                    <button
                        onClick={handleClone}
                        disabled={isCloning}
                        className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
                    >
                        <Copy className="h-4 w-4" />
                        {isCloning ? 'Cloning...' : 'Clone to My Agents'}
                    </button>
                </div>
            </DialogContent>
        </Dialog>
    );
});

export default AgentDetailModal;
