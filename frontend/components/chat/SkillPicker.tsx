'use client';

import { memo, useMemo } from 'react';
import { Lightning, Check } from '@phosphor-icons/react';
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from '@/components/ui/popover';
import {
    Command,
    CommandEmpty,
    CommandGroup,
    CommandInput,
    CommandItem,
    CommandList,
} from '@/components/ui/command';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useSkills } from '@/hooks/useSkills';
import { cn } from '@/lib/utils';
import type { Skill } from '@/lib/api/skills';
import Link from 'next/link';

interface SkillPickerProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    initialQuery?: string;
    selectedSkillIds: string[];
    onSkillSelect: (skill: Skill) => void;
}

export const SkillPicker = memo(function SkillPicker({
    open,
    onOpenChange,
    initialQuery = '',
    selectedSkillIds,
    onSkillSelect,
}: SkillPickerProps) {
    const { data: skills, isLoading } = useSkills();

    const groupedSkills = useMemo(() => {
        if (!skills) return {};
        const groups: Record<string, Skill[]> = {};
        for (const skill of skills) {
            const cat = skill.category || 'other';
            if (!groups[cat]) groups[cat] = [];
            groups[cat].push(skill);
        }
        return groups;
    }, [skills]);

    const handleSelect = (skill: Skill) => {
        if (!selectedSkillIds.includes(skill.id)) {
            onSkillSelect(skill);
        }
        onOpenChange(false);
    };

    return (
        <Popover open={open} onOpenChange={onOpenChange}>
            <Tooltip>
                <TooltipTrigger asChild>
                    <PopoverTrigger asChild>
                        <button
                            className={cn(
                                'h-8 w-8 flex items-center justify-center rounded-lg',
                                'transition-colors duration-150',
                                'text-neutral-400 hover:text-neutral-600 hover:bg-neutral-100'
                            )}
                            aria-label="Select skills"
                        >
                            <Lightning className="h-[18px] w-[18px]" weight="regular" />
                        </button>
                    </PopoverTrigger>
                </TooltipTrigger>
                <TooltipContent side="top" sideOffset={8}>
                    <p>Select skills</p>
                </TooltipContent>
            </Tooltip>

            <PopoverContent side="top" align="start" className="w-[360px] p-0">
                <Command shouldFilter={true}>
                    <CommandInput
                        placeholder="Search skills..."
                        defaultValue={initialQuery}
                    />
                    <CommandList className="max-h-[320px]">
                        <CommandEmpty>No skills found.</CommandEmpty>

                        {isLoading && (
                            <div className="px-4 py-6 text-center text-sm text-neutral-400">
                                Loading skills...
                            </div>
                        )}

                        {!isLoading && Object.entries(groupedSkills).map(([category, categorySkills]) => (
                            <CommandGroup key={category} heading={category.charAt(0).toUpperCase() + category.slice(1)}>
                                {categorySkills.map((skill) => {
                                    const isSelected = selectedSkillIds.includes(skill.id);
                                    return (
                                        <CommandItem
                                            key={skill.id}
                                            value={skill.name}
                                            onSelect={() => handleSelect(skill)}
                                            className="flex items-center gap-2"
                                        >
                                            <Lightning
                                                className={cn(
                                                    'h-4 w-4 shrink-0',
                                                    isSelected ? 'text-neutral-900' : 'text-neutral-400'
                                                )}
                                                weight={isSelected ? 'fill' : 'regular'}
                                            />
                                            <div className="flex-1 min-w-0">
                                                <div className="text-sm font-medium truncate">{skill.name}</div>
                                                {skill.description && (
                                                    <div className="text-xs text-neutral-400 truncate">{skill.description}</div>
                                                )}
                                            </div>
                                            {isSelected && (
                                                <Check className="h-4 w-4 text-neutral-900 shrink-0" />
                                            )}
                                        </CommandItem>
                                    );
                                })}
                            </CommandGroup>
                        ))}
                    </CommandList>
                </Command>

                <div className="px-4 py-2.5 border-t border-neutral-100">
                    <Link
                        href="/settings/skills"
                        className="text-xs text-neutral-600 hover:text-neutral-900 font-medium"
                    >
                        Manage skills
                    </Link>
                </div>
            </PopoverContent>
        </Popover>
    );
});
