
import { apiFetch } from './index';

export interface Skill {
    id: string;
    name: string;
    description: string;
    category: string;
    is_custom: boolean;
    is_system?: boolean;
    enabled: boolean;
    skill_dependencies?: string[];
    requires?: string[];
    mcp_dependencies: string[];
    author: string;
    version: string;
    code?: string | null;
    configuration?: Record<string, any>;
    created_at?: string | null;
    updated_at?: string | null;
}

export type SkillCreate = Omit<Skill, 'id' | 'created_at' | 'updated_at' | 'is_custom' | 'enabled'>;

export const skillsApi = {
    /**
     * List all available skills (system and custom)
     */
    listSkills: async (category?: string): Promise<Skill[]> => {
        const queryParams = new URLSearchParams();
        if (category) {
            queryParams.append('category', category);
        }
        const query = queryParams.toString();
        return apiFetch<Skill[]>(`/api/v1/skills${query ? `?${query}` : ''}`);
    },

    /**
     * Get skill details by ID
     */
    getSkill: async (skillId: string): Promise<Skill> => {
        return apiFetch<Skill>(`/api/v1/skills/${skillId}`);
    },

    /**
     * Create a new custom skill
     */
    createSkill: async (skillFn: SkillCreate): Promise<Skill> => {
        return apiFetch<Skill>('/api/v1/skills', {
            method: 'POST',
            body: JSON.stringify(skillFn),
        });
    },

    /**
     * List available categories
     */
    listCategories: async (): Promise<string[]> => {
        return apiFetch<string[]>('/api/v1/skills/categories');
    }
};
