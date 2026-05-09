
'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import { SkillEditor } from '@/components/skills/SkillEditor';
import { ArrowLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';
// import { skillsApi } from '@/lib/api/skills'; // Assume this exists or will be updated

export default function NewSkillPage() {
    const router = useRouter();

    const handleSave = async (code: string) => {
        try {
            // For now, just logging/toasting as backend creation endpoint might need to be verified
            // await skillsApi.create({ code });
            console.log("Saving skill:", code);
            toast('Success', { description: 'Skill saved successfully (Simulation)' });
            router.push('/skills');
        } catch (error) {
            toast.error('Error', { description: 'Failed to save skill' });
        }
    };

    return (
        <div className="container mx-auto py-8">
            <Button variant="ghost" onClick={() => router.back()} className="mb-6">
                <ArrowLeft className="w-4 h-4 mr-2" /> Back
            </Button>

            <div className="mb-8">
                <h1 className="text-3xl font-bold">Create New Skill</h1>
                <p className="text-muted-foreground">Define your custom skill using Python and Frontmatter metadata.</p>
            </div>

            <SkillEditor onSave={handleSave} />
        </div>
    );
}
