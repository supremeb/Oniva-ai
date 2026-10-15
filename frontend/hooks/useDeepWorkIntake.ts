/**
 * useDeepWorkIntake — starts the Deep Work intake (clarify -> plan -> approve -> start)
 * on the right runtime for a chat surface.
 *
 * Flag ENABLE_ENGINE_DEEP_WORK on: the intake calls the engine API with the chat's project
 * (the page's project, else the user's first active one, as EngineChat does) and the run is
 * published to the engine session store on start. Off: the legacy store flow, unchanged.
 */
import { useCallback, useEffect } from 'react';
import { useEngineDeepWorkMode } from '@/lib/hooks/useFeature';
import { useProjectsStore } from '@/lib/stores/projects';
import { useDeepWorkStore } from '@/lib/stores/deep-work';

export function useDeepWorkIntake(projectId?: string) {
  const mode = useEngineDeepWorkMode();
  const projects = useProjectsStore((s) => s.projects);
  const loadProjects = useProjectsStore((s) => s.loadProjects);
  useEffect(() => {
    if (mode === 'engine' && !projectId && (projects?.length ?? 0) === 0) void loadProjects();
  }, [mode, projectId, projects?.length, loadProjects]);
  const resolvedProjectId = projectId ?? (projects ?? []).find((p) => !p.is_archived)?.id ?? null;

  const startIntake = useCallback(
    (query: string, chatId: string, kbIds: string[], connectorIds: string[]) => {
      if (mode !== 'engine') {
        return useDeepWorkStore.getState().clarify(query, chatId, kbIds, connectorIds);
      }
      if (!resolvedProjectId) {
        useDeepWorkStore.setState({
          engineMode: true,
          status: 'error',
          conversationId: chatId,
          enginePendingQuery: null,
          error: 'Create a project first to use Deep Work.',
        });
        return Promise.resolve();
      }
      return useDeepWorkStore
        .getState()
        .clarify(query, chatId, kbIds, connectorIds, { engine: { projectId: resolvedProjectId } });
    },
    [mode, resolvedProjectId],
  );

  return { mode, startIntake };
}
