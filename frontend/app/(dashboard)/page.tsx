'use client';

import { memo, useState, useEffect } from 'react';
import Link from 'next/link';
import { useAgents, AGENT_STYLES, getAgentStyle, type AgentInfo } from '@/lib/api/agents';
import { BUILTIN_AGENT_UUIDS } from '@/lib/builtin-agents';
import {
    TreeStructure,
    ChatCircle,
    GearSix,
    Database,
    ChartLineUp,
    ShieldCheck,
    BookOpenText,
    IdentificationCard,
    Warning,
    FileText,
    Robot,
    ArrowRight,
    Plugs,
    Lightbulb,
} from '@phosphor-icons/react';

// Fallback template agents when backend is unavailable
const FALLBACK_AGENTS: AgentInfo[] = [
    { id: BUILTIN_AGENT_UUIDS['data-retrieval'], name: 'Data Retrieval Agent', type: 'data-retrieval', description: 'Fetches and aggregates financial data from multiple sources', status: 'active' },
    { id: BUILTIN_AGENT_UUIDS['analysis'], name: 'Analysis Agent', type: 'analysis', description: 'Performs financial analysis and calculations', status: 'active' },
    { id: BUILTIN_AGENT_UUIDS['compliance'], name: 'Compliance Agent', type: 'compliance', description: 'Checks regulatory compliance and risk assessment', status: 'active' },
    { id: BUILTIN_AGENT_UUIDS['research'], name: 'Research Agent', type: 'research', description: 'RAG-based document research and Q&A', status: 'active' },
];

// Icon mapping
const ICONS: Record<string, React.ElementType> = {
    'Database': Database,
    'ChartLineUp': ChartLineUp,
    'ShieldCheck': ShieldCheck,
    'BookOpenText': BookOpenText,
    'IdentificationCard': IdentificationCard,
    'Warning': Warning,
    'FileText': FileText,
};

// Quick action card component
interface QuickActionProps {
    href: string;
    icon: React.ElementType;
    title: string;
    description: string;
}

const QuickAction = memo(function QuickAction({ href, icon: Icon, title, description }: QuickActionProps) {
    return (
        <Link href={href} className="group">
            <div className="h-full p-4 rounded-lg border border-border hover:border-foreground/20 transition-colors duration-150">
                <div className="flex items-start gap-3">
                    <div className="w-9 h-9 rounded-lg bg-muted flex items-center justify-center shrink-0">
                        <Icon className="h-[18px] w-[18px] text-muted-foreground" weight="regular" />
                    </div>
                    <div className="min-w-0 flex-1">
                        <h3 className="text-card-title">{title}</h3>
                        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                            {description}
                        </p>
                    </div>
                </div>
            </div>
        </Link>
    );
});

// Getting started step component
interface StepProps {
    number: number;
    title: string;
    description: string;
}

const Step = memo(function Step({ number, title, description }: StepProps) {
    return (
        <div className="flex items-start gap-3 py-2">
            <div className="w-6 h-6 rounded-full bg-muted text-muted-foreground font-medium flex items-center justify-center text-xs shrink-0">
                {number}
            </div>
            <div>
                <p className="text-sm text-foreground">{title}</p>
                <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
            </div>
        </div>
    );
});

export default function DashboardPage() {
    const { data: agents, isLoading, isError } = useAgents();
    const [showFallback, setShowFallback] = useState(false);

    useEffect(() => {
        const timer = setTimeout(() => {
            setShowFallback(true);
        }, 1500);
        return () => clearTimeout(timer);
    }, []);

    const displayAgents = agents?.length ? agents : FALLBACK_AGENTS;
    const hasAgents = agents && Array.isArray(agents) && agents.length > 0;
    const actuallyLoading = isLoading && !isError && !showFallback && !hasAgents;

    return (
        <div className="p-6 lg:p-8 max-w-7xl mx-auto">
            {/* Header */}
            <div className="flex items-center justify-between mb-8">
                <div>
                    <h1 className="text-page-title">
                        Welcome to Oniva
                    </h1>
                    <p className="text-page-description mt-1">
                        Build and execute composable AI agent workflows.
                    </p>
                </div>
            </div>

            {/* Quick Actions */}
            <section className="mb-10">
                <h2 className="text-overline mb-4">Quick Actions</h2>
                <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
                    <QuickAction
                        href="/workflows"
                        icon={TreeStructure}
                        title="Workflow Builder"
                        description="Create visual workflows with drag-and-drop"
                    />
                    <QuickAction
                        href="/agents"
                        icon={Robot}
                        title="AI Agents"
                        description="Browse and configure agent templates"
                    />
                    <QuickAction
                        href="/chat"
                        icon={ChatCircle}
                        title="Chat Interface"
                        description="Test agents in real-time conversations"
                    />
                    <QuickAction
                        href="/connectors"
                        icon={Plugs}
                        title="Connectors"
                        description="Integrate external data sources"
                    />
                </div>
            </section>

            {/* Available Agents */}
            <section className="mb-10">
                <div className="flex items-center justify-between mb-4">
                    <h2 className="text-overline">Available Agents</h2>
                    <Link href="/agents" className="text-xs text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1">
                        View all
                        <ArrowRight className="h-3 w-3" />
                    </Link>
                </div>

                {actuallyLoading ? (
                    <div className="flex items-center justify-center py-12">
                        <div className="w-5 h-5 border-2 border-muted-foreground/20 border-t-muted-foreground rounded-full animate-spin" />
                    </div>
                ) : (
                    <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
                        {displayAgents.map((agent) => {
                            const style = getAgentStyle(agent.id);
                            const IconComponent = ICONS[style.icon] || ShieldCheck;

                            return (
                                <Link key={agent.id} href={`/agents/${agent.id}`} className="group">
                                    <div className="p-3 rounded-lg border border-border hover:border-foreground/20 transition-colors duration-150">
                                        <div className="flex items-center gap-3">
                                            <div className="w-8 h-8 rounded-lg bg-muted flex items-center justify-center shrink-0">
                                                <IconComponent className="h-4 w-4 text-muted-foreground" weight="regular" />
                                            </div>
                                            <div className="min-w-0 flex-1">
                                                <h3 className="text-card-title truncate">
                                                    {agent.name.replace(' Agent', '')}
                                                </h3>
                                                <p className="text-xs text-muted-foreground truncate">
                                                    {agent.description.slice(0, 35)}...
                                                </p>
                                            </div>
                                        </div>
                                    </div>
                                </Link>
                            );
                        })}
                    </div>
                )}
            </section>

            {/* Getting Started & Quick Links */}
            <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div className="rounded-lg border border-border p-5">
                    <div className="flex items-center gap-2 mb-4">
                        <Lightbulb className="h-4 w-4 text-muted-foreground" weight="regular" />
                        <h3 className="font-medium text-sm">Getting Started</h3>
                    </div>
                    <div className="space-y-1">
                        <Step
                            number={1}
                            title="Configure API Keys"
                            description="Add your LLM provider keys in settings"
                        />
                        <Step
                            number={2}
                            title="Explore Agents"
                            description="Browse pre-built agents or create custom ones"
                        />
                        <Step
                            number={3}
                            title="Build Workflows"
                            description="Drag and drop agents to create pipelines"
                        />
                        <Step
                            number={4}
                            title="Execute & Iterate"
                            description="Run workflows and refine based on results"
                        />
                    </div>
                </div>

                <div className="rounded-lg border border-border p-5">
                    <div className="flex items-center gap-2 mb-4">
                        <BookOpenText className="h-4 w-4 text-muted-foreground" weight="regular" />
                        <h3 className="font-medium text-sm">Quick Links</h3>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                        <Link
                            href="/settings/api-keys"
                            className="flex items-center gap-2 p-3 rounded-lg border border-border hover:border-foreground/20 transition-colors text-sm"
                        >
                            <GearSix className="h-4 w-4 text-muted-foreground" />
                            API Keys
                        </Link>
                        <Link
                            href="/skills"
                            className="flex items-center gap-2 p-3 rounded-lg border border-border hover:border-foreground/20 transition-colors text-sm"
                        >
                            <Lightbulb className="h-4 w-4 text-muted-foreground" />
                            Skills
                        </Link>
                        <Link
                            href="/docs"
                            className="flex items-center gap-2 p-3 rounded-lg border border-border hover:border-foreground/20 transition-colors text-sm"
                        >
                            <BookOpenText className="h-4 w-4 text-muted-foreground" />
                            Documentation
                        </Link>
                        <Link
                            href="/workflows"
                            className="flex items-center gap-2 p-3 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors text-sm"
                        >
                            <TreeStructure className="h-4 w-4" />
                            New Workflow
                        </Link>
                    </div>
                </div>
            </section>
        </div>
    );
}
