
'use client';

import React, { useState, useEffect } from 'react';
import { Skill, skillsApi } from '@/lib/api/skills';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
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
import { ArrowUp, ArrowDown, X, Plus, Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { ScrollArea } from '@/components/ui/scroll-area';

interface SkillSelectorProps {
    value: string[]; // List of skill IDs or names (using names as IDs for now if system skills lack UUIDs in strict sense, but we added UUIDs. Let's assume names for persistence if easier, or IDs. Backend uses ID? The prompt earlier used names in manifests. `skills.ts` uses ID. Let's use IDs for value.)
    onChange: (skills: string[]) => void;
    inheritDefaults: boolean;
    onInheritChange: (checked: boolean) => void;
    className?: string;
}

export function SkillSelector({
    value = [],
    onChange,
    inheritDefaults,
    onInheritChange,
    className
}: SkillSelectorProps) {
    const [open, setOpen] = useState(false);
    const [availableSkills, setAvailableSkills] = useState<Skill[]>([]);
    const [skillMap, setSkillMap] = useState<Record<string, Skill>>({});

    useEffect(() => {
        const loadSkills = async () => {
            try {
                const data = await skillsApi.listSkills();
                setAvailableSkills(data);
                const map: Record<string, Skill> = {};
                data.forEach(s => map[s.id] = s);
                setSkillMap(map);
            } catch (e) {
                console.error("Failed to load skills", e);
            }
        };
        loadSkills();
    }, []);

    const handleSelect = (skillId: string) => {
        if (value.includes(skillId)) return;
        onChange([...value, skillId]);
        setOpen(false);
    };

    const handleRemove = (skillId: string) => {
        onChange(value.filter(id => id !== skillId));
    };

    const moveUp = (index: number) => {
        if (index === 0) return;
        const newValue = [...value];
        [newValue[index - 1], newValue[index]] = [newValue[index], newValue[index - 1]];
        onChange(newValue);
    };

    const moveDown = (index: number) => {
        if (index === value.length - 1) return;
        const newValue = [...value];
        [newValue[index + 1], newValue[index]] = [newValue[index], newValue[index + 1]];
        onChange(newValue);
    };

    // Helper to fallback to ID if map not ready
    const getSkillName = (id: string) => skillMap[id]?.name || id;
    const getSkillCategory = (id: string) => skillMap[id]?.category || 'unknown';

    return (
        <div className={cn("space-y-4", className)}>
            <div className="flex items-center space-x-2">
                <Switch
                    id="inherit-skills"
                    checked={inheritDefaults}
                    onCheckedChange={onInheritChange}
                />
                <Label htmlFor="inherit-skills">Inherit Default Skills</Label>
            </div>

            <div className="space-y-2">
                <Label>Assigned Skills (Ordered by Priority)</Label>

                <div className="border rounded-md p-2 min-h-[100px] space-y-2 bg-background">
                    {value.length === 0 ? (
                        <div className="text-sm text-muted-foreground text-center py-8">
                            No specific skills assigned.
                            {inheritDefaults && " (Developing agent defaults apply)"}
                        </div>
                    ) : (
                        value.map((skillId, index) => (
                            <div
                                key={skillId}
                                className="flex items-center justify-between p-2 rounded-md border bg-card text-card-foreground shadow-sm group"
                            >
                                <div className="flex items-center gap-2 overflow-hidden">
                                    <span className="text-xs text-muted-foreground font-mono w-4">{index + 1}</span>
                                    <Badge variant="outline" className="capitalize shrink-0">
                                        {getSkillCategory(skillId)}
                                    </Badge>
                                    <span className="truncate text-sm font-medium" title={getSkillName(skillId)}>
                                        {getSkillName(skillId)}
                                    </span>
                                    {/* Warning if missing from map (deleted skill?) */}
                                    {!skillMap[skillId] && availableSkills.length > 0 && (
                                        <span className="text-destructive text-xs" title="Skill not found">⚠️</span>
                                    )}
                                </div>

                                <div className="flex items-center gap-1 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="h-6 w-6"
                                        onClick={() => moveUp(index)}
                                        disabled={index === 0}
                                    >
                                        <ArrowUp className="h-3 w-3" />
                                    </Button>
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="h-6 w-6"
                                        onClick={() => moveDown(index)}
                                        disabled={index === value.length - 1}
                                    >
                                        <ArrowDown className="h-3 w-3" />
                                    </Button>
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="h-6 w-6 text-destructive hover:text-destructive"
                                        onClick={() => handleRemove(skillId)}
                                    >
                                        <X className="h-3 w-3" />
                                    </Button>
                                </div>
                            </div>
                        ))
                    )}
                </div>

                <Popover open={open} onOpenChange={setOpen}>
                    <PopoverTrigger asChild>
                        <Button variant="outline" size="sm" className="w-full border-dashed">
                            <Plus className="mr-2 h-4 w-4" />
                            Add Skill
                        </Button>
                    </PopoverTrigger>
                    <PopoverContent className="p-0 w-[400px]" align="start">
                        <Command>
                            <CommandInput placeholder="Search skills..." />
                            <CommandList>
                                <CommandEmpty>No skills found.</CommandEmpty>
                                <CommandGroup heading="Available Skills">
                                    <ScrollArea className="h-[200px]">
                                        {availableSkills.map((skill) => {
                                            const isSelected = value.includes(skill.id);
                                            if (isSelected) return null; // Hide selected? Or show disabled. Hide is cleaner.
                                            return (
                                                <CommandItem
                                                    key={skill.id}
                                                    value={skill.name} // Search by name
                                                    onSelect={() => handleSelect(skill.id)}
                                                >
                                                    <div className="flex items-center justify-between w-full">
                                                        <div className="flex items-center gap-2">
                                                            <Badge variant="secondary" className="text-[10px] capitalize w-16 justify-center">
                                                                {skill.category}
                                                            </Badge>
                                                            <span>{skill.name}</span>
                                                            {skill.mock && (
                                                                <Badge variant="outline" className="text-[10px] border-amber-500 text-amber-700">Mock</Badge>
                                                            )}
                                                        </div>
                                                        {isSelected && <Check className="h-4 w-4" />}
                                                    </div>
                                                </CommandItem>
                                            );
                                        })}
                                    </ScrollArea>
                                </CommandGroup>
                            </CommandList>
                        </Command>
                    </PopoverContent>
                </Popover>
            </div>
        </div>
    );
}
