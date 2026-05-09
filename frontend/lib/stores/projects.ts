/**
 * Projects Zustand store.
 * Backend-driven — no localStorage persistence.
 * Uses TanStack Query for cache + server sync.
 */
import { create } from 'zustand';
import type { Project } from '@/lib/models';
import {
  fetchProjects,
  fetchProject,
  createProject as apiCreateProject,
  updateProject as apiUpdateProject,
  archiveProject as apiArchiveProject,
} from '@/lib/api/projects';

interface ProjectsStore {
  // State
  projects: Project[];
  currentProjectId: string | null;
  isLoading: boolean;
  error: string | null;

  // Actions
  loadProjects: (includeArchived?: boolean) => Promise<void>;
  loadProject: (projectId: string) => Promise<Project | null>;
  createProject: (name: string, description?: string) => Promise<Project>;
  updateProject: (
    projectId: string,
    data: {
      name?: string;
      description?: string;
      instructions?: string;
      default_model?: string;
      row_version: number;
    }
  ) => Promise<Project>;
  archiveProject: (projectId: string, isArchived: boolean) => Promise<void>;
  setCurrentProject: (projectId: string | null) => void;
  getProject: (projectId: string) => Project | undefined;
}

export const useProjectsStore = create<ProjectsStore>()((set, get) => ({
  projects: [],
  currentProjectId: null,
  isLoading: false,
  error: null,

  loadProjects: async (includeArchived = false) => {
    set({ isLoading: true, error: null });
    try {
      const result = await fetchProjects({
        page: 1,
        page_size: 100,
        include_archived: includeArchived,
      });
      set({ projects: result.projects, isLoading: false });
    } catch (err) {
      set({ error: (err as Error).message, isLoading: false });
    }
  },

  loadProject: async (projectId: string) => {
    try {
      const project = await fetchProject(projectId);
      set((state) => ({
        projects: state.projects.map((p) =>
          p.id === projectId ? project : p
        ),
      }));
      return project;
    } catch {
      return null;
    }
  },

  createProject: async (name: string, description?: string) => {
    const project = await apiCreateProject({ name, description });
    set((state) => ({
      projects: [project, ...state.projects],
      currentProjectId: project.id,
    }));
    return project;
  },

  updateProject: async (projectId, data) => {
    const project = await apiUpdateProject(projectId, data);
    set((state) => ({
      projects: state.projects.map((p) =>
        p.id === projectId ? project : p
      ),
    }));
    return project;
  },

  archiveProject: async (projectId: string, isArchived: boolean) => {
    await apiArchiveProject(projectId, isArchived);
    set((state) => ({
      projects: state.projects.filter((p) => p.id !== projectId),
      currentProjectId:
        state.currentProjectId === projectId ? null : state.currentProjectId,
    }));
  },

  setCurrentProject: (projectId: string | null) => {
    set({ currentProjectId: projectId });
  },

  getProject: (projectId: string) => {
    return get().projects.find((p) => p.id === projectId);
  },
}));
