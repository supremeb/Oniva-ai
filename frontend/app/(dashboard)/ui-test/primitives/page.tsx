'use client';

import { useState } from 'react';
import {
    ArrowsClockwise,
    ChartLine,
    ChatCircleDots,
    Database,
    Lightning,
    Plus,
    Robot,
    ShieldCheck,
    Sparkle,
    TreeStructure,
    Users,
} from '@phosphor-icons/react';

import { Button } from '@/components/ui/button';
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import { MetricRow } from '@/components/ui/metric-row';
import { PageHeader } from '@/components/ui/page-header';
import { SectionCard } from '@/components/ui/section-card';
import { StatCard } from '@/components/ui/stat-card';

export default function PrimitivesShowcase() {
    const [count, setCount] = useState(0);

    return (
        <div className="mx-auto max-w-6xl px-6 py-8 space-y-12">
            <PageHeader
                eyebrow="Design system"
                title="Primitives showcase"
                description="Living visual contract for the page-level layout primitives. Use this page when reviewing design fidelity for any wave-rollout PR."
                size="hero"
                actions={
                    <>
                        <Button variant="outline" size="sm">
                            <ArrowsClockwise className="h-4 w-4" weight="bold" />
                            Refresh
                        </Button>
                        <Button size="sm">
                            <Plus className="h-4 w-4" weight="bold" />
                            New
                        </Button>
                    </>
                }
            />

            {/* Card variants */}
            <section className="space-y-4">
                <h2 className="text-lg font-semibold tracking-tight">Card variants</h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                    <Card className="p-5">
                        <p className="text-xs uppercase tracking-wide text-muted-foreground/80">
                            default
                        </p>
                        <p className="mt-2 text-sm">
                            Standard surface with shadow + border. Existing usages remain
                            byte-identical.
                        </p>
                    </Card>

                    <Card variant="interactive" className="p-5">
                        <p className="text-xs uppercase tracking-wide text-muted-foreground/80">
                            interactive
                        </p>
                        <p className="mt-2 text-sm">
                            Hover to lift, press to scale to 0.96. Canonical transition. Try
                            tabbing — focus ring uses design tokens.
                        </p>
                    </Card>

                    <Card variant="inset" className="p-5">
                        <p className="text-xs uppercase tracking-wide text-muted-foreground/80">
                            inset
                        </p>
                        <p className="mt-2 text-sm">
                            For nesting inside default cards. Smaller radius, no outer shadow
                            — concentric pairing with the parent.
                        </p>
                    </Card>

                    <Card variant="hero" className="p-5">
                        <p className="text-xs uppercase tracking-wide text-muted-foreground/80">
                            hero
                        </p>
                        <p className="mt-2 text-sm">
                            Larger radius and shadow for top-of-page surfaces.
                        </p>
                    </Card>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <Card surface="outlined" className="p-5">
                        <p className="text-xs uppercase tracking-wide text-muted-foreground/80">
                            surface=outlined
                        </p>
                        <p className="mt-2 text-sm">
                            Replaces shadow with a 2px border for flat layouts.
                        </p>
                    </Card>
                    <Card variant="hero" className="p-5">
                        <CardHeader className="p-0">
                            <CardTitle>Concentric radii</CardTitle>
                            <CardDescription>
                                hero (rounded-2xl) outside, inset (rounded-lg) inside.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="p-0 pt-4">
                            <Card variant="inset" className="p-3">
                                <p className="text-xs text-muted-foreground">
                                    Nested inset card.
                                </p>
                            </Card>
                        </CardContent>
                    </Card>
                </div>
            </section>

            {/* MetricRow + StatCard */}
            <section className="space-y-4">
                <h2 className="text-lg font-semibold tracking-tight">
                    MetricRow + StatCard
                </h2>
                <MetricRow columns={4}>
                    <StatCard
                        label="Active agents"
                        value="42"
                        delta={0.12}
                        icon={<Robot className="h-5 w-5" weight="duotone" />}
                        hint="vs. last week"
                    />
                    <StatCard
                        label="Workflows run"
                        value="1,284"
                        delta={-0.034}
                        icon={<TreeStructure className="h-5 w-5" weight="duotone" />}
                        hint="last 24h"
                    />
                    <StatCard
                        label="Compliance pass rate"
                        value="98.4%"
                        delta={0.008}
                        icon={<ShieldCheck className="h-5 w-5" weight="duotone" />}
                        hint="rolling 30d"
                    />
                    <StatCard
                        label="Avg latency"
                        value="184ms"
                        icon={<Lightning className="h-5 w-5" weight="duotone" />}
                        href="/observability"
                        hint="click to view"
                    />
                </MetricRow>

                <MetricRow columns={3}>
                    <StatCard
                        label="Connected sources"
                        value="12"
                        icon={<Database className="h-5 w-5" weight="duotone" />}
                        onClick={() => setCount((c) => c + 1)}
                        hint={`Clicked ${count} ${count === 1 ? 'time' : 'times'}`}
                    />
                    <StatCard
                        label="Members"
                        value="8"
                        icon={<Users className="h-5 w-5" weight="duotone" />}
                    />
                    <StatCard
                        label="Cost (MTD)"
                        value="$1,240.50"
                        delta={0.21}
                        icon={<ChartLine className="h-5 w-5" weight="duotone" />}
                    />
                </MetricRow>
            </section>

            {/* SectionCard */}
            <section className="space-y-4">
                <h2 className="text-lg font-semibold tracking-tight">SectionCard</h2>
                <SectionCard
                    title="Recent activity"
                    description="Last 5 workflow runs across the tenant."
                    actions={
                        <Button variant="ghost" size="sm">
                            View all
                        </Button>
                    }
                >
                    <ul className="divide-y divide-border/40 -my-2">
                        {['Quarterly compliance', 'Bank reconciliation', 'Customer KYC'].map(
                            (item) => (
                                <li
                                    key={item}
                                    className="flex items-center justify-between py-3 text-sm"
                                >
                                    <span>{item}</span>
                                    <span className="text-xs text-muted-foreground tabular-nums">
                                        2m ago
                                    </span>
                                </li>
                            ),
                        )}
                    </ul>
                </SectionCard>

                <SectionCard
                    title="Flush header"
                    description="No divider between header and body."
                    flushHeader
                >
                    <p className="text-sm text-muted-foreground">
                        Useful when the body has its own visual separation (e.g., a chart with
                        gridlines).
                    </p>
                </SectionCard>
            </section>

            {/* EmptyState */}
            <section className="space-y-4">
                <h2 className="text-lg font-semibold tracking-tight">EmptyState</h2>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <Card className="overflow-hidden">
                        <EmptyState
                            size="md"
                            iconTreatment="plain"
                            icon={<Sparkle className="h-8 w-8" weight="duotone" />}
                            title="No items yet"
                            description="Create your first item to see it here."
                            action={
                                <Button size="sm">
                                    <Plus className="h-4 w-4" weight="bold" />
                                    Create item
                                </Button>
                            }
                        />
                    </Card>

                    <Card className="overflow-hidden">
                        <EmptyState
                            size="md"
                            iconTreatment="gradient"
                            gradientClassName="from-violet-500/20 to-indigo-500/20"
                            icon={
                                <ChatCircleDots
                                    className="h-8 w-8 text-violet-600"
                                    weight="duotone"
                                />
                            }
                            title="Start a conversation"
                            description="Pick an agent and ask anything."
                            suggestions={['Summarize Q3', 'Run compliance check', 'Show top errors']}
                            onSuggestionClick={() => undefined}
                        />
                    </Card>
                </div>

                <Card className="overflow-hidden">
                    <EmptyState
                        size="sm"
                        iconTreatment="plain"
                        icon={<Sparkle className="h-6 w-6" weight="duotone" />}
                        title="Compact empty state"
                        description="Use inside narrow panels or sidebars."
                    />
                </Card>
            </section>

            {/* PageHeader sizes */}
            <section className="space-y-4">
                <h2 className="text-lg font-semibold tracking-tight">PageHeader sizes</h2>
                <Card className="p-6">
                    <PageHeader
                        size="compact"
                        eyebrow="Settings"
                        title="API keys"
                        description="Manage tenant-level API keys."
                        actions={<Button size="sm">New key</Button>}
                    />
                </Card>
                <Card className="p-6">
                    <PageHeader
                        size="default"
                        eyebrow="Workflows"
                        title="All workflows"
                        description="Build, test, and deploy multi-agent workflows."
                        actions={<Button size="sm">New workflow</Button>}
                    />
                </Card>
            </section>
        </div>
    );
}
