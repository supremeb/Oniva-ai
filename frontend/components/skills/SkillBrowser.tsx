
'use client';

import React, { useState, useEffect, useMemo } from 'react';
import { Skill, skillsApi } from '@/lib/api/skills';
import { SkillCard } from './SkillCard';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Search, Filter, RefreshCw, AlertCircle } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Skeleton } from '@/components/ui/skeleton'; // Assuming Skeleton exists or I'll use div
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';

export function SkillBrowser() {
    const [skills, setSkills] = useState<Skill[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [searchQuery, setSearchQuery] = useState('');
    const [selectedCategory, setSelectedCategory] = useState<string>('all');
    const [categories, setCategories] = useState<string[]>([]);
    const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);

    const fetchSkills = async () => {
        setLoading(true);
        setError(null);
        try {
            const [skillsData, catsData] = await Promise.all([
                skillsApi.listSkills(),
                skillsApi.listCategories()
            ]);
            setSkills(skillsData);
            setCategories(catsData);
        } catch (err: any) {
            setError(err.message || 'Failed to fetch skills');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchSkills();
    }, []);

    const filteredSkills = useMemo(() => {
        return skills.filter(skill => {
            const matchesSearch =
                skill.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
                skill.description.toLowerCase().includes(searchQuery.toLowerCase());

            const matchesCategory = selectedCategory === 'all' || skill.category === selectedCategory;

            return matchesSearch && matchesCategory;
        });
    }, [skills, searchQuery, selectedCategory]);

    return (
        <div className="space-y-6">
            <div className="flex flex-col sm:flex-row gap-4 justify-between items-start sm:items-center">
                <div className="relative flex-1 w-full sm:max-w-md">
                    <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder="Search skills..."
                        className="pl-8"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                    />
                </div>

                <div className="flex gap-2 w-full sm:w-auto">
                    <Select value={selectedCategory} onValueChange={setSelectedCategory}>
                        <SelectTrigger className="w-[180px]">
                            <Filter className="w-4 h-4 mr-2" />
                            <SelectValue placeholder="Category" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="all">All Categories</SelectItem>
                            {categories.map(cat => (
                                <SelectItem key={cat} value={cat} className="capitalize">
                                    {cat}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>

                    <Button variant="outline" size="icon" onClick={fetchSkills} title="Refresh">
                        <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                    </Button>
                </div>
            </div>

            {error && (
                <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4" />
                    <AlertTitle>Error</AlertTitle>
                    <AlertDescription>{error}</AlertDescription>
                </Alert>
            )}

            {loading ? (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                    {[...Array(8)].map((_, i) => (
                        <div key={i} className="h-[200px] rounded-xl border border-muted bg-card text-card-foreground shadow space-y-4 p-4">
                            <div className="h-4 bg-muted rounded w-1/2 animate-pulse" />
                            <div className="h-20 bg-muted rounded animate-pulse" />
                            <div className="h-4 bg-muted rounded w-1/3 animate-pulse" />
                        </div>
                    ))}
                </div>
            ) : (
                <>
                    {filteredSkills.length === 0 ? (
                        <div className="text-center py-12 text-muted-foreground">
                            No skills found matching your criteria.
                        </div>
                    ) : (
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                            {filteredSkills.map(skill => (
                                <SkillCard
                                    key={skill.id}
                                    skill={skill}
                                    onSelect={setSelectedSkill}
                                />
                            ))}
                        </div>
                    )}
                </>
            )}

            <Dialog open={!!selectedSkill} onOpenChange={(open) => !open && setSelectedSkill(null)}>
                <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
                    {selectedSkill && (
                        <>
                            <DialogHeader>
                                <div className="flex items-center gap-2 mb-2">
                                    <Badge variant="outline" className="capitalize">{selectedSkill.category}</Badge>
                                    {selectedSkill.is_custom && <Badge>Custom</Badge>}
                                </div>
                                <DialogTitle className="text-2xl">{selectedSkill.name}</DialogTitle>
                                <DialogDescription className="text-base mt-2">
                                    {selectedSkill.description}
                                </DialogDescription>
                            </DialogHeader>

                            <ScrollArea className="flex-1 pr-4">
                                <div className="space-y-6 py-4">
                                    {/* Details Grid */}
                                    <div className="grid grid-cols-2 gap-4 text-sm">
                                        <div>
                                            <span className="text-muted-foreground block mb-1">Author</span>
                                            <span className="font-medium">{selectedSkill.author}</span>
                                        </div>
                                        <div>
                                            <span className="text-muted-foreground block mb-1">Version</span>
                                            <span className="font-medium">{selectedSkill.version}</span>
                                        </div>
                                        <div>
                                            <span className="text-muted-foreground block mb-1">Dependencies</span>
                                            {(selectedSkill.skill_dependencies?.length ?? 0) > 0 || (selectedSkill.requires?.length ?? 0) > 0 ? (
                                                <div className="flex flex-wrap gap-1">
                                                    {(selectedSkill.skill_dependencies || selectedSkill.requires || []).map(d => (
                                                        <Badge key={d} variant="secondary" className="text-xs">{d}</Badge>
                                                    ))}
                                                </div>
                                            ) : (
                                                <span className="text-muted-foreground italic">None</span>
                                            )}
                                        </div>
                                        <div>
                                            <span className="text-muted-foreground block mb-1">MCP Dependencies</span>
                                            {(selectedSkill.mcp_dependencies?.length ?? 0) > 0 ? (
                                                <div className="flex flex-wrap gap-1">
                                                    {(selectedSkill.mcp_dependencies || []).map(d => (
                                                        <Badge key={d} variant="outline" className="text-xs">{d}</Badge>
                                                    ))}
                                                </div>
                                            ) : (
                                                <span className="text-muted-foreground italic">None</span>
                                            )}
                                        </div>
                                    </div>

                                    {/* Config Schema (if we had it in full details, currently simple) */}
                                    {/* <div>
                            <h4 className="font-medium mb-2">Configuration</h4>
                            <pre className="bg-muted p-2 rounded text-xs overflow-x-auto">
                                {JSON.stringify(selectedSkill.configuration, null, 2)}
                            </pre>
                        </div> */}

                                    {selectedSkill.code && (
                                        <div>
                                            <h4 className="font-medium mb-2">Source Code</h4>
                                            <pre className="bg-muted p-2 rounded text-xs overflow-x-auto max-h-[200px]">
                                                {selectedSkill.code}
                                            </pre>
                                        </div>
                                    )}
                                </div>
                            </ScrollArea>
                        </>
                    )}
                </DialogContent>
            </Dialog>
        </div>
    );
}
