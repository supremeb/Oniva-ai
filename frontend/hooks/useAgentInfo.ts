import { useMemo } from 'react';
import { useAgents } from '@/hooks/useAgents';
import { useCustomAgents, getAgentStyle } from '@/lib/api/agents';
import { isBuiltinAgentId } from '@/lib/builtin-agents';

export interface AgentDisplayInfo {
  name: string;
  icon: string;
  color: string;
  bgColor: string;
}

/**
 * Resolve an agent ID (built-in or custom) to display info for the chat header.
 * Returns null if agentId is falsy or the agent can't be found.
 */
export function useAgentInfo(agentId: string | null | undefined): AgentDisplayInfo | null {
  const { data: builtinAgents } = useAgents();
  const { data: customAgents } = useCustomAgents();

  return useMemo(() => {
    if (!agentId) return null;

    // Check built-in agents first
    if (isBuiltinAgentId(agentId)) {
      const style = getAgentStyle(agentId);
      const agent = builtinAgents?.find((a) => a.id === agentId);
      if (agent) {
        return { name: agent.name, icon: style.icon, color: style.color, bgColor: style.bgColor };
      }
      // Fallback: check custom agents list (built-in agents are also seeded there)
      const custom = customAgents?.find((a) => a.id === agentId);
      if (custom) {
        return { name: custom.name, icon: style.icon, color: style.color, bgColor: style.bgColor };
      }
      return null;
    }

    // Custom agent lookup
    const custom = customAgents?.find((a) => a.id === agentId);
    if (custom) {
      return {
        name: custom.name,
        icon: custom.icon || 'Robot',
        color: 'text-muted-foreground',
        bgColor: 'bg-muted',
      };
    }

    return null;
  }, [agentId, builtinAgents, customAgents]);
}
