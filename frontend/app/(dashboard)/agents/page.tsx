'use client';

import { useState, useEffect, memo, useCallback } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import { ConfirmationDialog } from '@/components/ui/confirmation-dialog';
import {
    useAgents,
    useCustomAgents,
    useOrganizationAgents,
    useOrganizationCategories,
    AGENT_STYLES,
    getAgentStyle,
    createNewAgentAsync,
    cloneAgentAsync,
    deleteCustomAgentAsync,
    seedExampleAgents,
    CustomAgent
} from '@/lib/api/agents';
import { useQueryClient } from '@tanstack/react-query';
import { BUILTIN_AGENT_UUIDS } from '@/lib/builtin-agents';
import {
    Database,
    ChartLineUp,
    ShieldCheck,
    BookOpenText,
    IdentificationCard,
    Warning,
    FileText,
    Plus,
    Robot,
    Copy,
    Trash,
    DotsThree,
    MagnifyingGlass,
    ChatCircle,
    Buildings,
    ArrowDown,
    Tag,
    Funnel,
    Globe,
    Sparkle,
} from '@phosphor-icons/react';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Badge } from '@/components/ui/badge';
import { useAgentVersions } from '@/hooks/useAgentVersions';
import AgentDetailModal from '@/components/agents/AgentDetailModal';

// Icon mapping
const ICONS: Record<string, React.ElementType> = {
    'Database': Database,
    'ChartLineUp': ChartLineUp,
    'ShieldCheck': ShieldCheck,
    'BookOpenText': BookOpenText,
    'IdentificationCard': IdentificationCard,
    'Warning': Warning,
    'FileText': FileText,
    'Robot': Robot,
    'ChatCircle': ChatCircle,
};

type Tab = 'my-agents' | 'organization' | 'built-in';

// Custom agent card
interface CustomAgentCardProps {
    agent: CustomAgent;
    onDelete: (id: string) => void;
}

const CustomAgentCard = memo(function CustomAgentCard({ agent, onDelete }: CustomAgentCardProps) {
    const router = useRouter();
    const IconComponent = ICONS[agent.icon] || Robot;
    const isLocal = agent.id.startsWith('local-');
    const { data: versionData } = useAgentVersions(agent.id);
    // Get deployed version display string
    const deployedVersion = versionData?.current_deployed ||
        (versionData?.versions?.length
            ? (versionData.versions[0].version_number ? `v${versionData.versions[0].version_number}` : versionData.versions[0].legacy_version)
            : null);

    return (
        <Link href={`/agents/${agent.id}`}>
            <div className="p-4 rounded-lg border border-border hover:border-foreground/20 transition-colors group">
                <div className="flex items-start justify-between mb-3">
                    <div className="w-8 h-8 rounded-lg bg-muted flex items-center justify-center">
                        <IconComponent className="h-4 w-4 text-muted-foreground" weight="regular" />
                    </div>
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <button
                                aria-label={`More options for ${agent.name}`}
                                className="p-1.5 rounded-md opacity-0 group-hover:opacity-100 hover:bg-muted transition-all"
                                onClick={(e) => e.stopPropagation()}
                            >
                                <DotsThree className="h-4 w-4 text-muted-foreground" weight="bold" aria-hidden="true" />
                            </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={(e) => { e.stopPropagation(); router.push(`/agents/${agent.id}`); }}>
                                Edit
                            </DropdownMenuItem>
                            <DropdownMenuItem
                                className="text-destructive focus:text-destructive"
                                onClick={(e) => { e.preventDefault(); e.stopPropagation(); onDelete(agent.id); }}
                            >
                                <Trash className="mr-2 h-4 w-4" />
                                Delete
                            </DropdownMenuItem>
                        </DropdownMenuContent>
                    </DropdownMenu>
                </div>
                <h3 className="text-card-title truncate">{agent.name}</h3>
                <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">
                    {agent.description || 'No description'}
                </p>
                <div className="mt-3 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
                            Custom
                        </span>
                        {agent.visibility && agent.visibility !== 'private' && (
                            <Badge variant="outline" className="text-[10px] py-0 h-5 gap-0.5 text-green-600 border-green-200 dark:border-green-800">
                                <Globe className="h-2.5 w-2.5" />
                                Published
                            </Badge>
                        )}
                        {agent.flows.length > 0 && (
                            <span className="text-xs text-muted-foreground">
                                {agent.flows.length} flow{agent.flows.length > 1 ? 's' : ''}
                            </span>
                        )}
                    </div>
                    {/* Version/Local badge - consistent with workflow cards */}
                    <div className="flex items-center gap-1.5">
                        {isLocal ? (
                            <Badge variant="outline" className="text-[10px] py-0 h-5 text-muted-foreground">
                                Local
                            </Badge>
                        ) : deployedVersion ? (
                            <Badge variant="outline" className="text-[10px] py-0 h-5 gap-0.5">
                                {deployedVersion}
                            </Badge>
                        ) : (
                            <Badge variant="outline" className="text-[10px] py-0 h-5 gap-0.5">
                                v1
                            </Badge>
                        )}
                    </div>
                </div>
            </div>
        </Link>
    );
});

// Built-in agent card (default system agents)
interface BuiltInAgentCardProps {
    agent: {
        id: string;
        name: string;
        description: string;
        type: string;
        status: string;
    };
    onClone: (id: string) => void;
}

const BuiltInAgentCard = memo(function BuiltInAgentCard({ agent, onClone }: BuiltInAgentCardProps) {
    const style = getAgentStyle(agent.id);
    const IconComponent = ICONS[style.icon] || ShieldCheck;

    return (
        <Link href={`/agents/${agent.id}`}>
            <div className="p-4 rounded-lg border border-border hover:border-foreground/20 transition-colors group">
                <div className="flex items-start justify-between mb-3">
                    <div className="w-8 h-8 rounded-lg bg-muted flex items-center justify-center">
                        <IconComponent className="h-4 w-4 text-muted-foreground" weight="regular" />
                    </div>
                    <button
                        className="flex items-center gap-1 px-2 py-1 rounded-md text-xs text-muted-foreground opacity-0 group-hover:opacity-100 hover:bg-muted transition-all"
                        onClick={(e) => { e.preventDefault(); e.stopPropagation(); onClone(agent.id); }}
                    >
                        <Copy className="h-3 w-3" />
                        Clone
                    </button>
                </div>
                <h3 className="text-card-title truncate">{agent.name}</h3>
                <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">
                    {agent.description}
                </p>
                <div className="mt-3">
                    <span className="text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
                        Built-in
                    </span>
                </div>
            </div>
        </Link>
    );
});

// Organization agent card (browsable from registry)
interface OrgAgentCardProps {
    agent: CustomAgent;
    onView: (agent: CustomAgent) => void;
}

const OrgAgentCard = memo(function OrgAgentCard({ agent, onView }: OrgAgentCardProps) {
    const IconComponent = ICONS[agent.icon] || Robot;

    return (
        <button
            type="button"
            className="w-full text-left p-4 rounded-lg border border-border hover:border-foreground/20 transition-colors group"
            onClick={() => onView(agent)}
        >
            <div className="flex items-start justify-between mb-3">
                <div className="w-8 h-8 rounded-lg bg-muted flex items-center justify-center">
                    <IconComponent className="h-4 w-4 text-muted-foreground" weight="regular" />
                </div>
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                    <ArrowDown className="h-3 w-3" />
                    {agent.cloneCount ?? 0}
                </div>
            </div>
            <h3 className="text-card-title truncate">{agent.name}</h3>
            <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">
                {agent.description || 'No description'}
            </p>
            <div className="mt-3 flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                    {agent.category && (
                        <span className="text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
                            {agent.category}
                        </span>
                    )}
                </div>
                {agent.complianceTags && agent.complianceTags.length > 0 && (
                    <div className="flex items-center gap-1">
                        <Tag className="h-3 w-3 text-muted-foreground" />
                        <span className="text-[10px] text-muted-foreground">
                            {agent.complianceTags.length}
                        </span>
                    </div>
                )}
            </div>
        </button>
    );
});

// Fallback built-in agents (default system agents)
const FALLBACK_BUILTIN_AGENTS = [
    { id: BUILTIN_AGENT_UUIDS['general'], name: 'General Assistant', type: 'general', description: 'Conversational AI helper - handles greetings and guides to specialized agents', status: 'available' },
    { id: BUILTIN_AGENT_UUIDS['data-retrieval'], name: 'Data Retrieval Agent', type: 'data', description: 'Fetches and aggregates financial data from multiple sources', status: 'available' },
    { id: BUILTIN_AGENT_UUIDS['analysis'], name: 'Analysis Agent', type: 'analysis', description: 'Performs financial analysis and calculations', status: 'available' },
    { id: BUILTIN_AGENT_UUIDS['compliance'], name: 'Compliance Agent', type: 'compliance', description: 'Checks regulatory compliance and risk assessment', status: 'available' },
    { id: BUILTIN_AGENT_UUIDS['research'], name: 'Research Agent', type: 'research', description: 'RAG-based document research and Q&A', status: 'available' },
];

export default function AgentsPage() {
    const router = useRouter();
    const { data: templateAgents, isLoading, error } = useAgents();
    const { data: customAgents = [] } = useCustomAgents();
    const queryClient = useQueryClient();
    const [isHydrated, setIsHydrated] = useState(false);
    const [createDialogOpen, setCreateDialogOpen] = useState(false);
    const [newAgentName, setNewAgentName] = useState('');
    const [newAgentDescription, setNewAgentDescription] = useState('');
    const [searchQuery, setSearchQuery] = useState('');
    const [showFallback, setShowFallback] = useState(false);
    const [isCreating, setIsCreating] = useState(false);
    const [activeTab, setActiveTab] = useState<Tab>('my-agents');

    // Organization tab state
    const [orgCategory, setOrgCategory] = useState<string | undefined>();
    const [orgSort, setOrgSort] = useState<string>('clone_count');
    const [selectedAgent, setSelectedAgent] = useState<CustomAgent | null>(null);
    const [detailModalOpen, setDetailModalOpen] = useState(false);
    const [deleteAgentId, setDeleteAgentId] = useState<string | null>(null);

    // Organization data
    const { data: orgData, isLoading: orgLoading } = useOrganizationAgents({
        query: activeTab === 'organization' ? searchQuery || undefined : undefined,
        category: orgCategory,
        sortBy: orgSort,
        enabled: activeTab === 'organization',
    });
    const { data: categories = [] } = useOrganizationCategories(activeTab === 'organization');

    useEffect(() => {
        const timer = setTimeout(() => setShowFallback(true), 1500);
        return () => clearTimeout(timer);
    }, []);

    useEffect(() => {
        seedExampleAgents();
        setIsHydrated(true);
    }, []);

    const displayBuiltInAgents = templateAgents?.length ? templateAgents : FALLBACK_BUILTIN_AGENTS;
    const hasTemplates = templateAgents && Array.isArray(templateAgents) && templateAgents.length > 0;
    const actuallyLoading = isLoading && !error && !showFallback && !hasTemplates;

    const handleCreateAgent = async () => {
        if (!newAgentName.trim()) return;
        setIsCreating(true);
        try {
            const agent = await createNewAgentAsync(newAgentName, newAgentDescription);
            await queryClient.invalidateQueries({ queryKey: ['customAgents'] });
            setNewAgentName('');
            setNewAgentDescription('');
            setCreateDialogOpen(false);
            router.push(`/agents/${agent.id}`);
        } catch (error) {
            console.error('Failed to create agent:', error);
        } finally {
            setIsCreating(false);
        }
    };

    const handleCloneAgent = async (templateId: string) => {
        const template = templateAgents?.find(a => a.id === templateId);
        if (!template) return;
        try {
            const cloned = await cloneAgentAsync(templateId, `${template.name} (Copy)`);
            if (cloned) {
                await queryClient.invalidateQueries({ queryKey: ['customAgents'] });
                router.push(`/agents/${cloned.id}`);
            }
        } catch (error) {
            console.error('Failed to clone agent:', error);
        }
    };

    const handleDeleteAgent = useCallback((id: string) => {
        setDeleteAgentId(id);
    }, []);

    const confirmDeleteAgent = async () => {
        if (!deleteAgentId) return;
        await deleteCustomAgentAsync(deleteAgentId);
        await queryClient.invalidateQueries({ queryKey: ['customAgents'] });
    };

    const handleViewOrgAgent = (agent: CustomAgent) => {
        setSelectedAgent(agent);
        setDetailModalOpen(true);
    };

    const handleOrgCloned = () => {
        queryClient.invalidateQueries({ queryKey: ['customAgents'] });
        queryClient.invalidateQueries({ queryKey: ['organizationAgents'] });
        setActiveTab('my-agents');
    };

    const filteredCustomAgents = customAgents.filter(agent =>
        agent.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        agent.description?.toLowerCase().includes(searchQuery.toLowerCase())
    );

    const filteredBuiltInAgents = displayBuiltInAgents.filter(agent =>
        agent.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        agent.description.toLowerCase().includes(searchQuery.toLowerCase())
    );

    return (
        <div className="p-6 lg:p-8 max-w-7xl mx-auto">
            {/* Header */}
            <div className="flex items-center justify-between mb-6">
                <div>
                    <h1 className="text-page-title">Agents</h1>
                    <p className="text-page-description mt-1">
                        Configure and customize your AI agents
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        onClick={() => router.push('/agents/create')}
                        className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors"
                    >
                        <Sparkle className="h-4 w-4" weight="fill" />
                        Create with AI
                    </button>
                    <Dialog open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
                    <DialogTrigger asChild>
                        <button className="flex items-center gap-2 px-3 py-2 border border-border rounded-lg text-sm font-medium text-muted-foreground hover:text-foreground hover:border-foreground/20 transition-colors">
                            <Plus className="h-4 w-4" />
                            Manual
                        </button>
                    </DialogTrigger>
                    <DialogContent className="sm:max-w-md">
                        <DialogHeader>
                            <DialogTitle>Create New Agent</DialogTitle>
                            <DialogDescription>
                                Create a custom agent with your own configuration.
                            </DialogDescription>
                        </DialogHeader>
                        <div className="space-y-4 py-4">
                            <div className="space-y-2">
                                <Label htmlFor="name">Name</Label>
                                <Input
                                    id="name"
                                    placeholder="My Custom Agent"
                                    value={newAgentName}
                                    onChange={(e) => setNewAgentName(e.target.value)}
                                />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="description">Description</Label>
                                <Textarea
                                    id="description"
                                    placeholder="What does this agent do?"
                                    value={newAgentDescription}
                                    onChange={(e) => setNewAgentDescription(e.target.value)}
                                    rows={3}
                                    className="resize-none"
                                />
                            </div>
                        </div>
                        <DialogFooter>
                            <button
                                onClick={() => setCreateDialogOpen(false)}
                                className="px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleCreateAgent}
                                disabled={!newAgentName.trim() || isCreating}
                                className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
                            >
                                <Plus className="h-4 w-4" />
                                {isCreating ? 'Creating...' : 'Create'}
                            </button>
                        </DialogFooter>
                    </DialogContent>
                </Dialog>
                </div>
            </div>

            {/* Tabs */}
            <div role="tablist" aria-label="Agent categories" className="flex gap-1 mb-6 border-b border-border">
                <button
                    role="tab"
                    aria-selected={activeTab === 'my-agents'}
                    onClick={() => setActiveTab('my-agents')}
                    className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
                        activeTab === 'my-agents'
                            ? 'border-primary text-primary'
                            : 'border-transparent text-muted-foreground hover:text-foreground'
                    }`}
                >
                    <Robot size={18} aria-hidden="true" />
                    My Agents
                    {isHydrated && customAgents.length > 0 && (
                        <span className="ml-1 px-1.5 py-0.5 text-xs rounded-full bg-primary/10 text-primary" aria-label={`${customAgents.length} agents`}>
                            {customAgents.length}
                        </span>
                    )}
                </button>
                <button
                    role="tab"
                    aria-selected={activeTab === 'organization'}
                    onClick={() => setActiveTab('organization')}
                    className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
                        activeTab === 'organization'
                            ? 'border-primary text-primary'
                            : 'border-transparent text-muted-foreground hover:text-foreground'
                    }`}
                >
                    <Buildings size={18} aria-hidden="true" />
                    Organization
                    {orgData && orgData.total > 0 && (
                        <span className="ml-1 px-1.5 py-0.5 text-xs rounded-full bg-primary/10 text-primary" aria-label={`${orgData.total} agents`}>
                            {orgData.total}
                        </span>
                    )}
                </button>
                <button
                    role="tab"
                    aria-selected={activeTab === 'built-in'}
                    onClick={() => setActiveTab('built-in')}
                    className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
                        activeTab === 'built-in'
                            ? 'border-primary text-primary'
                            : 'border-transparent text-muted-foreground hover:text-foreground'
                    }`}
                >
                    <ShieldCheck size={18} aria-hidden="true" />
                    Built-in
                    <span className="ml-1 px-1.5 py-0.5 text-xs rounded-full bg-primary/10 text-primary" aria-label={`${displayBuiltInAgents.length} agents`}>
                        {displayBuiltInAgents.length}
                    </span>
                </button>
            </div>

            {/* Search */}
            <div className="relative max-w-md mb-6">
                <MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                    aria-label={
                        activeTab === 'organization'
                            ? 'Search organization agents'
                            : activeTab === 'built-in'
                                ? 'Search built-in agents'
                                : 'Search my agents'
                    }
                    placeholder={
                        activeTab === 'organization'
                            ? 'Search organization agents...'
                            : activeTab === 'built-in'
                                ? 'Search built-in agents...'
                                : 'Search my agents...'
                    }
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="pl-9"
                />
            </div>

            {/* My Agents Tab */}
            {activeTab === 'my-agents' && (
                <section suppressHydrationWarning>
                    {!isHydrated ? (
                        <div className="flex items-center justify-center py-12">
                            <div className="w-5 h-5 border-2 border-muted-foreground/20 border-t-muted-foreground rounded-full animate-spin" />
                        </div>
                    ) : filteredCustomAgents.length === 0 ? (
                        <div className="text-center py-12 border border-dashed border-border rounded-lg">
                            <Robot className="h-8 w-8 text-muted-foreground mx-auto mb-3" weight="light" />
                            <p className="text-sm text-muted-foreground">
                                {searchQuery ? 'No custom agents match your search' : 'No custom agents yet. Create one to get started.'}
                            </p>
                        </div>
                    ) : (
                        <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
                            {filteredCustomAgents.map((agent) => (
                                <CustomAgentCard
                                    key={agent.id}
                                    agent={agent}
                                    onDelete={handleDeleteAgent}
                                />
                            ))}
                        </div>
                    )}
                </section>
            )}

            {/* Organization Tab */}
            {activeTab === 'organization' && (
                <section>
                    {/* Category filter chips */}
                    {categories.length > 0 && (
                        <div className="flex items-center gap-2 mb-4 flex-wrap">
                            <Funnel className="h-4 w-4 text-muted-foreground" />
                            <button
                                onClick={() => setOrgCategory(undefined)}
                                className={`px-3 py-1 text-xs rounded-full transition-colors ${
                                    !orgCategory
                                        ? 'bg-primary text-primary-foreground'
                                        : 'bg-muted text-muted-foreground hover:text-foreground'
                                }`}
                            >
                                All
                            </button>
                            {categories.map((cat) => (
                                <button
                                    key={cat.name}
                                    onClick={() => setOrgCategory(orgCategory === cat.name ? undefined : cat.name)}
                                    className={`px-3 py-1 text-xs rounded-full transition-colors ${
                                        orgCategory === cat.name
                                            ? 'bg-primary text-primary-foreground'
                                            : 'bg-muted text-muted-foreground hover:text-foreground'
                                    }`}
                                >
                                    {cat.name} ({cat.count})
                                </button>
                            ))}
                        </div>
                    )}

                    {/* Sort options */}
                    <div className="flex items-center gap-2 mb-4">
                        <span className="text-xs text-muted-foreground">Sort by:</span>
                        {[
                            { value: 'clone_count', label: 'Most cloned' },
                            { value: 'recent', label: 'Recent' },
                            { value: 'name', label: 'Name' },
                        ].map((opt) => (
                            <button
                                key={opt.value}
                                onClick={() => setOrgSort(opt.value)}
                                className={`text-xs px-2 py-1 rounded transition-colors ${
                                    orgSort === opt.value
                                        ? 'text-foreground font-medium'
                                        : 'text-muted-foreground hover:text-foreground'
                                }`}
                            >
                                {opt.label}
                            </button>
                        ))}
                    </div>

                    {orgLoading ? (
                        <div className="flex items-center justify-center py-12">
                            <div className="w-5 h-5 border-2 border-muted-foreground/20 border-t-muted-foreground rounded-full animate-spin" />
                        </div>
                    ) : !orgData || orgData.agents.length === 0 ? (
                        <div className="text-center py-12 border border-dashed border-border rounded-lg">
                            <Buildings className="h-8 w-8 text-muted-foreground mx-auto mb-3" weight="light" />
                            <p className="text-sm text-muted-foreground">
                                {searchQuery
                                    ? 'No organization agents match your search'
                                    : 'No agents have been published to the organization yet.'}
                            </p>
                            <p className="text-xs text-muted-foreground mt-1">
                                Publish your agents to share them across teams.
                            </p>
                        </div>
                    ) : (
                        <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
                            {orgData.agents.map((agent) => (
                                <OrgAgentCard
                                    key={agent.id}
                                    agent={agent}
                                    onView={handleViewOrgAgent}
                                />
                            ))}
                        </div>
                    )}
                </section>
            )}

            {/* Built-in Tab */}
            {activeTab === 'built-in' && (
                <section>
                    {actuallyLoading ? (
                        <div className="flex items-center justify-center py-12">
                            <div className="w-5 h-5 border-2 border-muted-foreground/20 border-t-muted-foreground rounded-full animate-spin" />
                        </div>
                    ) : filteredBuiltInAgents.length === 0 ? (
                        <div className="text-center py-8 text-muted-foreground">
                            No built-in agents match your search
                        </div>
                    ) : (
                        <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
                            {filteredBuiltInAgents.map((agent) => (
                                <BuiltInAgentCard
                                    key={agent.id}
                                    agent={agent}
                                    onClone={handleCloneAgent}
                                />
                            ))}
                        </div>
                    )}
                </section>
            )}

            {/* Agent Detail Modal (for Organization tab) */}
            <AgentDetailModal
                agent={selectedAgent}
                open={detailModalOpen}
                onOpenChange={setDetailModalOpen}
                onCloned={handleOrgCloned}
            />

            <ConfirmationDialog
                open={!!deleteAgentId}
                onOpenChange={(open) => { if (!open) setDeleteAgentId(null); }}
                title="Delete Agent"
                description="This will permanently delete this agent. This action cannot be undone."
                onConfirm={confirmDeleteAgent}
            />
        </div>
    );
}
