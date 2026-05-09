'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ChatSidebar } from '@/components/chat/ChatSidebar';
import { ProjectView } from '@/components/chat/ProjectView';
import { ChatInterface } from '@/components/chat/ChatInterface';
import { useProjectsStore } from '@/lib/stores/projects';
import { useChatSessionsStore } from '@/lib/stores/chat-sessions';
import type { Project } from '@/lib/models';

export default function ProjectPage() {
    const params = useParams();
    const router = useRouter();
    const projectId = params.projectId as string;
    const { loadProject, setCurrentProject } = useProjectsStore();
    const { currentSessionId } = useChatSessionsStore();
    const [project, setProject] = useState<Project | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        setCurrentProject(projectId);
        loadProject(projectId).then((p) => {
            setProject(p);
            setLoading(false);
            if (!p) {
                router.push('/chat');
            }
        });
        return () => {
            setCurrentProject(null);
        };
    }, [projectId, loadProject, setCurrentProject, router]);

    if (loading) {
        return (
            <div className="h-full flex">
                <ChatSidebar />
                <div className="flex-1 flex items-center justify-center">
                    <div className="text-muted-foreground">Loading project...</div>
                </div>
            </div>
        );
    }

    if (!project) return null;

    return (
        <div className="h-full flex">
            <ChatSidebar />
            <div className="flex-1 min-w-0 flex flex-col h-full overflow-hidden">
                {currentSessionId ? (
                    <ChatInterface projectId={projectId} />
                ) : (
                    <ProjectView project={project} />
                )}
            </div>
        </div>
    );
}
