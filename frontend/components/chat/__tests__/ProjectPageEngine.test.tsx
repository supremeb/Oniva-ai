/** Project page: in engine mode an opened engine conversation renders the chat, not ProjectView. */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';

jest.mock('next/navigation', () => ({ useParams: () => ({ projectId: 'p1' }), useRouter: () => ({ push: jest.fn() }) }));
const mockMode = jest.fn();
jest.mock('@/lib/hooks/useFeature', () => ({ useEngineChatMode: () => mockMode() }));
jest.mock('@/components/chat/ChatSidebar', () => ({ ChatSidebar: () => <div /> }));
jest.mock('@/components/chat/ProjectView', () => ({ ProjectView: () => <div data-testid="project-view" /> }));
jest.mock('@/components/chat/ChatInterface', () => ({
    ChatInterface: ({ projectId, conversationId }: { projectId: string; conversationId?: string | null }) => (
        <div data-testid="chat" data-project={projectId} data-c={conversationId ?? ''} />
    ),
}));

import ProjectPage from '../../../app/(dashboard)/chat/projects/[projectId]/page';
import { useProjectsStore } from '@/lib/stores/projects';
import { useChatSessionsStore } from '@/lib/stores/chat-sessions';
import { useEngineNav } from '@/lib/stores/engine-nav';

beforeEach(() => {
    window.history.replaceState(null, '', '/chat/projects/p1');
    useChatSessionsStore.setState({ currentSessionId: null });
    useEngineNav.setState({ conversationId: null, nonce: 0 });
    useProjectsStore.setState({
        loadProject: jest.fn(async () => ({ id: 'p1', name: 'P' })),
        setCurrentProject: jest.fn(),
    } as never);
});

describe('project page engine awareness', () => {
    it('shows ProjectView by default', async () => {
        mockMode.mockReturnValue('engine');
        render(<ProjectPage />);
        await waitFor(() => expect(screen.getByTestId('project-view')).toBeTruthy());
    });

    it('renders the chat for ?c= in engine mode', async () => {
        mockMode.mockReturnValue('engine');
        window.history.replaceState(null, '', '/chat/projects/p1?c=eng1');
        render(<ProjectPage />);
        const chat = await screen.findByTestId('chat');
        expect(chat.getAttribute('data-project')).toBe('p1');
        expect(screen.queryByTestId('project-view')).toBeNull();
    });

    it('ignores ?c= in legacy mode', async () => {
        mockMode.mockReturnValue('legacy');
        window.history.replaceState(null, '', '/chat/projects/p1?c=eng1');
        render(<ProjectPage />);
        await waitFor(() => expect(screen.getByTestId('project-view')).toBeTruthy());
    });

    it('opens a sidebar-selected engine conversation', async () => {
        mockMode.mockReturnValue('engine');
        useEngineNav.setState({ conversationId: 'eng7', nonce: 1 });
        render(<ProjectPage />);
        const chat = await screen.findByTestId('chat');
        expect(chat.getAttribute('data-c')).toBe('eng7');
    });

    it('starting a chat (legacy session set) in engine mode renders the chat', async () => {
        mockMode.mockReturnValue('engine');
        useChatSessionsStore.setState({ currentSessionId: 's1' });
        render(<ProjectPage />);
        expect(await screen.findByTestId('chat')).toBeTruthy();
    });
});
