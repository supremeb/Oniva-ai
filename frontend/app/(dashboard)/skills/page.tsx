'use client';

import Link from 'next/link';
import { Plus } from '@phosphor-icons/react';
import { SkillBrowser } from '@/components/skills/SkillBrowser';

export default function SkillsPage() {
    return (
        <div className="p-6 lg:p-8 max-w-7xl mx-auto">
            {/* Header */}
            <div className="flex items-center justify-between mb-8">
                <div>
                    <h1 className="text-page-title">Skills</h1>
                    <p className="text-page-description mt-1">
                        Browse available capabilities for your agents
                    </p>
                </div>
                <Link href="/skills/new">
                    <button className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors">
                        <Plus className="h-4 w-4" />
                        Create Skill
                    </button>
                </Link>
            </div>

            <SkillBrowser />
        </div>
    );
}
