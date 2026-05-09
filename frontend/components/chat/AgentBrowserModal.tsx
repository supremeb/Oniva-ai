'use client';

import { useState, useMemo } from 'react';
import { MagnifyingGlass } from '@phosphor-icons/react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { useAgents } from '@/hooks/useAgents';
import { useCustomAgents, useOrganizationAgents, getAgentStyle } from '@/lib/api/agents';
import type { CustomAgent, AgentInfo } from '@/lib/api/agents';
import { isBuiltinAgentId, BUILTIN_AGENT_UUIDS } from '@/lib/builtin-agents';
import { getAgentIcon } from '@/lib/agent-icons';
import { cn } from '@/lib/utils';

interface AgentBrowserModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelectAgent: (agentId: string) => void;
}

export function AgentBrowserModal({ open, onOpenChange, onSelectAgent }: AgentBrowserModalProps) {
  const [search, setSearch] = useState('');
  const [activeTab, setActiveTab] = useState('my-agents');

  const { data: builtinAgents } = useAgents();
  const { data: customAgents } = useCustomAgents();
  const { data: orgData } = useOrganizationAgents({
    query: search || undefined,
    enabled: open && activeTab === 'organization',
  });

  // My Agents: custom agents that aren't built-in
  const myAgents = useMemo(() => {
    if (!customAgents) return [];
    return customAgents.filter((a) => !isBuiltinAgentId(a.id));
  }, [customAgents]);

  const filteredMyAgents = useMemo(() => {
    if (!search) return myAgents;
    const q = search.toLowerCase();
    return myAgents.filter(
      (a) => a.name.toLowerCase().includes(q) || a.description?.toLowerCase().includes(q)
    );
  }, [myAgents, search]);

  // Built-in agents, excluding General Assistant
  const filteredBuiltin = useMemo(() => {
    const agents = builtinAgents ?? [];
    const filtered = agents.filter((a) => a.id !== BUILTIN_AGENT_UUIDS['general']);
    if (!search) return filtered;
    const q = search.toLowerCase();
    return filtered.filter(
      (a) => a.name.toLowerCase().includes(q) || a.description?.toLowerCase().includes(q)
    );
  }, [builtinAgents, search]);

  const orgAgents = orgData?.agents ?? [];

  const handleSelect = (agentId: string) => {
    onSelectAgent(agentId);
    onOpenChange(false);
    setSearch('');
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[85vh] flex flex-col p-0 gap-0">
        <DialogHeader className="px-6 pt-6 pb-4">
          <DialogTitle>Browse Agents</DialogTitle>
        </DialogHeader>

        {/* Search */}
        <div className="px-6 pb-3">
          <div className="relative">
            <MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              placeholder="Search agents..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full h-9 pl-9 pr-3 rounded-md border border-input bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1"
            />
          </div>
        </div>

        {/* Tabs */}
        <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col min-h-0 px-6 pb-6">
          <TabsList className="w-full justify-start">
            <TabsTrigger value="my-agents">My Agents</TabsTrigger>
            <TabsTrigger value="organization">Organization</TabsTrigger>
            <TabsTrigger value="built-in">Built-in</TabsTrigger>
          </TabsList>

          <TabsContent value="my-agents" className="flex-1 min-h-0">
            <ScrollArea className="h-[50vh]">
              <div className="grid grid-cols-2 gap-2 pr-3">
                {filteredMyAgents.length === 0 ? (
                  <EmptyState message={search ? 'No agents match your search' : 'No custom agents yet'} />
                ) : (
                  filteredMyAgents.map((agent) => (
                    <CustomAgentCard key={agent.id} agent={agent} onSelect={handleSelect} />
                  ))
                )}
              </div>
            </ScrollArea>
          </TabsContent>

          <TabsContent value="organization" className="flex-1 min-h-0">
            <ScrollArea className="h-[50vh]">
              <div className="grid grid-cols-2 gap-2 pr-3">
                {orgAgents.length === 0 ? (
                  <EmptyState message={search ? 'No agents match your search' : 'No organization agents available'} />
                ) : (
                  orgAgents.map((agent) => (
                    <CustomAgentCard key={agent.id} agent={agent} onSelect={handleSelect} />
                  ))
                )}
              </div>
            </ScrollArea>
          </TabsContent>

          <TabsContent value="built-in" className="flex-1 min-h-0">
            <ScrollArea className="h-[50vh]">
              <div className="grid grid-cols-2 gap-2 pr-3">
                {filteredBuiltin.length === 0 ? (
                  <EmptyState message="No agents match your search" />
                ) : (
                  filteredBuiltin.map((agent) => (
                    <BuiltinAgentCard key={agent.id} agent={agent} onSelect={handleSelect} />
                  ))
                )}
              </div>
            </ScrollArea>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="col-span-2 flex items-center justify-center py-12 text-sm text-muted-foreground">
      {message}
    </div>
  );
}

function CustomAgentCard({
  agent,
  onSelect,
}: {
  agent: CustomAgent;
  onSelect: (id: string) => void;
}) {
  const IconComponent = getAgentIcon(agent.icon);

  return (
    <button
      onClick={() => onSelect(agent.id)}
      className="flex items-start gap-3 p-3 rounded-lg border border-border hover:bg-muted/50 transition-colors text-left w-full"
    >
      <div className="h-8 w-8 rounded-md bg-muted flex items-center justify-center shrink-0">
        <IconComponent className="h-4 w-4 text-muted-foreground" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium truncate">{agent.name}</span>
          <Badge variant="secondary" className="text-[10px] px-1.5 py-0 shrink-0">
            Custom
          </Badge>
        </div>
        {agent.description && (
          <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">{agent.description}</p>
        )}
      </div>
    </button>
  );
}

function BuiltinAgentCard({
  agent,
  onSelect,
}: {
  agent: AgentInfo;
  onSelect: (id: string) => void;
}) {
  const style = getAgentStyle(agent.id);
  const IconComponent = getAgentIcon(style.icon);

  return (
    <button
      onClick={() => onSelect(agent.id)}
      className="flex items-start gap-3 p-3 rounded-lg border border-border hover:bg-muted/50 transition-colors text-left w-full"
    >
      <div className={cn('h-8 w-8 rounded-md flex items-center justify-center shrink-0', style.bgColor)}>
        <IconComponent className={cn('h-4 w-4', style.color)} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium truncate">{agent.name}</span>
          <Badge variant="outline" className="text-[10px] px-1.5 py-0 shrink-0">
            Built-in
          </Badge>
        </div>
        {agent.description && (
          <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">{agent.description}</p>
        )}
      </div>
    </button>
  );
}
