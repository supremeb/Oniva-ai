import { useQuery } from '@tanstack/react-query';
import { getAgents, getAgent } from '@/lib/api/agents';

export function useAgents() {
  return useQuery({
    queryKey: ['agents'],
    queryFn: getAgents,
  });
}

export function useAgent(id: string) {
  return useQuery({
    queryKey: ['agents', id],
    queryFn: () => getAgent(id),
    enabled: !!id,
  });
}
