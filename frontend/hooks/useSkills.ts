import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { skillsApi, type Skill, type SkillCreate } from '@/lib/api/skills';

/**
 * Hook to fetch all skills with caching
 * Skills are cached for 5 minutes since they rarely change
 */
export function useSkills(category?: string) {
    return useQuery({
        queryKey: ['skills', category],
        queryFn: () => skillsApi.listSkills(category),
        staleTime: 5 * 60 * 1000, // 5 minutes
        gcTime: 30 * 60 * 1000, // 30 minutes cache
    });
}

/**
 * Hook to fetch a single skill by ID
 */
export function useSkill(skillId: string) {
    return useQuery({
        queryKey: ['skills', skillId],
        queryFn: () => skillsApi.getSkill(skillId),
        enabled: !!skillId,
        staleTime: 5 * 60 * 1000,
    });
}

/**
 * Hook to fetch skill categories
 */
export function useSkillCategories() {
    return useQuery({
        queryKey: ['skill-categories'],
        queryFn: () => skillsApi.listCategories(),
        staleTime: 10 * 60 * 1000, // Categories rarely change
    });
}

/**
 * Hook to create a new skill
 */
export function useCreateSkill() {
    const queryClient = useQueryClient();
    
    return useMutation({
        mutationFn: (skill: SkillCreate) => skillsApi.createSkill(skill),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['skills'] });
        },
    });
}
