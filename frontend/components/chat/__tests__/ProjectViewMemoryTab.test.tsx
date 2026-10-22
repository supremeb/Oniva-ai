import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('next/navigation', () => ({ useRouter: () => ({ push: jest.fn() }) }));
const mockFeature = jest.fn();
jest.mock('@/lib/hooks/useFeature', () => ({ useFeature: (n: string) => mockFeature(n) }));
jest.mock('@/hooks/useLLMProviders', () => ({ useAvailableModels: () => ({ models: [] }) }));
jest.mock('@/lib/stores/auth', () => ({
    useAuthStore: (sel: (s: unknown) => unknown) => sel({ user: { id: 'u1', role: 'admin' } }),
}));
jest.mock('@/lib/stores/projects', () => ({ useProjectsStore: () => ({ updateProject: jest.fn() }) }));
jest.mock('@/lib/stores/chat-sessions', () => ({
    useChatSessionsStore: () => ({
        createSession: jest.fn(),
        setCurrentSession: jest.fn(),
        selectedModel: null,
        setSelectedModel: jest.fn(),
        setPendingInput: jest.fn(),
    }),
}));
jest.mock('@/lib/stores/engine-nav', () => ({ useEngineNav: () => ({ open: jest.fn() }) }));
jest.mock('@/components/ui/resizable', () => ({
    ResizablePanelGroup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    ResizablePanel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    ResizableHandle: () => null,
}));
jest.mock('@/components/ui/scroll-area', () => ({
    ScrollArea: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
jest.mock('../SetInstructionsModal', () => ({ SetInstructionsModal: () => null }));
jest.mock('../ProjectMembersPanel', () => ({ ProjectMembersPanel: () => null }));
const mockUpdate = jest.fn();
jest.mock('@/lib/api/projects', () => ({
    uploadProjectFile: jest.fn(),
    fetchProjectFiles: jest.fn().mockResolvedValue({ files: [] }),
    deleteProjectFile: jest.fn(),
    fetchProjectSessions: jest.fn().mockResolvedValue({ sessions: [] }),
    updateProject: (...a: unknown[]) => mockUpdate(...a),
}));
jest.mock('../ProjectMemory', () => ({
    ProjectMemory: (p: { memoryEnabled: boolean; canPurge: boolean; onToggleEnabled: (n: boolean) => Promise<void> }) => (
        <div data-testid="memory" data-enabled={String(p.memoryEnabled)} data-purge={String(p.canPurge)}>
            <button onClick={() => p.onToggleEnabled(true)}>toggle</button>
        </div>
    ),
}));

import { ProjectView } from '../ProjectView';
import type { Project } from '@/lib/models';

const project = {
    id: 'p1',
    name: 'P',
    description: null,
    instructions: null,
    default_model: null,
    settings: { other: 1 },
    row_version: 4,
    member_count: 1,
    user_role: 'owner',
} as unknown as Project;

describe('ProjectView memory tab', () => {
    it('hides the tab entirely when the feature is off', () => {
        mockFeature.mockReturnValue(false);
        render(<ProjectView project={project} />);
        expect(screen.queryByRole('tab', { name: /memory/i })).toBeNull();
        expect(screen.queryByTestId('memory')).toBeNull();
    });

    it('shows the tab when the feature is on and writes settings.memory.enabled on toggle', async () => {
        mockFeature.mockImplementation((n: string) => n === 'ENABLE_PROJECT_MEMORY');
        mockUpdate.mockResolvedValue({ row_version: 5 });
        render(<ProjectView project={project} />);
        fireEvent.click(screen.getByRole('tab', { name: /memory/i }));
        const mem = screen.getByTestId('memory');
        expect(mem.getAttribute('data-enabled')).toBe('false');
        expect(mem.getAttribute('data-purge')).toBe('true');
        fireEvent.click(screen.getByText('toggle'));
        await waitFor(() =>
            expect(mockUpdate).toHaveBeenCalledWith('p1', {
                settings: { other: 1, memory: { enabled: true } },
                row_version: 4,
            })
        );
        await waitFor(() => expect(screen.getByTestId('memory').getAttribute('data-enabled')).toBe('true'));
    });
});
