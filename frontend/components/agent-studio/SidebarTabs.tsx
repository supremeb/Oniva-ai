'use client';

import { ReactNode } from 'react';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Package, TestTube, GearSix, Lightning } from '@phosphor-icons/react';
import {
    useAgentStudioLayout,
    type AgentStudioTab,
} from '@/lib/stores/agent-studio-layout';

interface SidebarTabsProps {
    resourcesContent: ReactNode;
    gradersContent: ReactNode;
    settingsContent: ReactNode;
    backgroundContent?: ReactNode;
    className?: string;
}

export function SidebarTabs({
    resourcesContent,
    gradersContent,
    settingsContent,
    backgroundContent,
    className,
}: SidebarTabsProps) {
    const { activeTab, setActiveTab } = useAgentStudioLayout();

    return (
        <div className="flex flex-col h-full">
            <Tabs
                value={activeTab}
                onValueChange={(v) => setActiveTab(v as AgentStudioTab)}
                className="flex flex-col h-full"
            >
                <div className="shrink-0 border-b px-3 py-2">
                    <TabsList className="w-full grid grid-cols-4 h-8">
                        <TabsTrigger
                            value="settings"
                            className="gap-1.5 text-[11px] leading-none data-[state=active]:text-foreground data-[state=active]:font-semibold"
                        >
                            <GearSix className="h-3.5 w-3.5" weight="duotone" />
                            Settings
                        </TabsTrigger>
                        <TabsTrigger
                            value="resources"
                            className="gap-1.5 text-[11px] leading-none data-[state=active]:text-foreground data-[state=active]:font-semibold"
                        >
                            <Package className="h-3.5 w-3.5" weight="duotone" />
                            Resources
                        </TabsTrigger>
                        <TabsTrigger
                            value="background"
                            className="gap-1.5 text-[11px] leading-none data-[state=active]:text-foreground data-[state=active]:font-semibold"
                        >
                            <Lightning className="h-3.5 w-3.5" weight="duotone" />
                            Background
                        </TabsTrigger>
                        <TabsTrigger
                            value="graders"
                            className="gap-1.5 text-[11px] leading-none data-[state=active]:text-foreground data-[state=active]:font-semibold"
                        >
                            <TestTube className="h-3.5 w-3.5" weight="duotone" />
                            Graders
                        </TabsTrigger>
                    </TabsList>
                </div>

                <div className="flex-1 min-h-0 overflow-hidden">
                    <TabsContent
                        value="settings"
                        className="h-full m-0 data-[state=inactive]:hidden"
                    >
                        {settingsContent}
                    </TabsContent>
                    <TabsContent
                        value="resources"
                        className="h-full m-0 data-[state=inactive]:hidden"
                    >
                        {resourcesContent}
                    </TabsContent>
                    <TabsContent
                        value="background"
                        className="h-full m-0 data-[state=inactive]:hidden"
                    >
                        {backgroundContent}
                    </TabsContent>
                    <TabsContent
                        value="graders"
                        className="h-full m-0 data-[state=inactive]:hidden"
                    >
                        {gradersContent}
                    </TabsContent>
                </div>
            </Tabs>
        </div>
    );
}
