
import React from 'react';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skill } from '@/lib/api/skills';
import { Layers, User, Tag, Puzzle } from 'lucide-react';

interface SkillCardProps {
    skill: Skill;
    onSelect?: (skill: Skill) => void;
    className?: string;
}

export function SkillCard({ skill, onSelect, className }: SkillCardProps) {
    // Determine badge variant based on category (mock logic)
    const getBadgeVariant = (cat: string) => {
        switch (cat.toLowerCase()) {
            case 'financial': return 'default';
            case 'compliance': return 'destructive'; // Use destructive for compliance/risk usually? Or separate variant.
            case 'code': return 'secondary';
            case 'web': return 'outline';
            default: return 'secondary';
        }
    };

    return (
        <Card className={`h-full flex flex-col ${className || ''} hover:shadow-md transition-shadow`}>
            <CardHeader className="pb-3">
                <div className="flex justify-between items-start gap-2">
                    <CardTitle className="text-lg font-bold truncate" title={skill.name}>
                        {skill.name}
                    </CardTitle>
                    <Badge variant={getBadgeVariant(skill.category)} className="shrink-0 capitalize">
                        {skill.category}
                    </Badge>
                </div>
                <div className="text-xs flex items-center gap-1 text-muted-foreground">
                    <span className="font-mono">v{skill.version}</span>
                    {skill.mock && (
                        <Badge variant="outline" className="text-[10px] h-4 px-1 py-0 border-amber-500 text-amber-700" title="Returns simulated data, not a live provider">Mock</Badge>
                    )}
                    {skill.is_custom ? (
                        <Badge variant="outline" className="text-[10px] h-4 px-1 py-0">Custom</Badge>
                    ) : (
                        <Badge variant="secondary" className="text-[10px] h-4 px-1 py-0 bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-100 border-none">System</Badge>
                    )}
                </div>
            </CardHeader>

            <CardContent className="flex-grow">
                <p className="text-body-muted line-clamp-3">
                    {skill.description}
                </p>

                <div className="mt-4 space-y-2">
                    {((skill.skill_dependencies?.length ?? 0) > 0 || (skill.requires?.length ?? 0) > 0) && (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground" title="Dependencies">
                            <Puzzle className="w-3 h-3" />
                            <span>Requires: {(skill.skill_dependencies || skill.requires || []).join(', ')}</span>
                        </div>
                    )}
                    <div className="flex items-center gap-2 text-xs text-muted-foreground" title="Author">
                        <User className="w-3 h-3" />
                        <span>{skill.author}</span>
                    </div>
                </div>
            </CardContent>

            <CardFooter className="pt-2">
                <Button
                    variant="outline"
                    className="w-full text-xs h-8"
                    onClick={() => onSelect?.(skill)}
                >
                    View Details
                </Button>
            </CardFooter>
        </Card>
    );
}
