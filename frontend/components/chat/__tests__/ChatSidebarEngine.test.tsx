/** Sidebar: engine conversations from the union list open with ?c=; legacy items keep their behavior. */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';

const push = jest.fn();
jest.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
const mockMode = jest.fn();
jest.mock('@/lib/hooks/useFeature', () => ({ useEngineChatMode: () => mockMode() }));
jest.mock('@/hooks/useAgents', () => ({ useAgents: () => ({ data: [] }) }));
jest.mock('../AgentBrowserModal', () => ({ AgentBrowserModal: () => null }));
jest.mock('@/hooks/useChatSessions', () => ({
    useChatSessionsList: () => ({
        data: [{ id: 'leg1', title: 'Legacy chat', is_pinned: false, updated_at: '2026-01-01T00:00:00Z' }],
        isLoading: false,
    }),
    useUpdateChatSession: () => ({ mutate: jest.fn() }),
    useDeleteChatSession: () => ({ mutate: jest.fn() }),
}));
const mockUnion = jest.fn();
jest.mock('@/hooks/useConversationUnion', () => ({ useConversationUnion: (enabled: boolean) => mockUnion(enabled) }));

import { ChatSidebar } from '../ChatSidebar';
import { useChatSessionsStore } from '@/lib/stores/chat-sessions';
import { useEngineNav } from '@/lib/stores/engine-nav';
import { useProjectsStore } from '@/lib/stores/projects';

const items = [
    { id: 'eng1', source: 'engine', title: 'Engine chat', updated_at: '2026-02-01T00:00:00Z', project_id: null },
    { id: 'eng2', source: 'engine', title: 'Project engine chat', updated_at: '2026-03-01T00:00:00Z', project_id: 'p9' },
    { id: 'leg1', source: 'legacy', title: 'Legacy chat', updated_at: '2026-01-01T00:00:00Z', project_id: null },
];

beforeEach(() => {
    push.mockClear();
    localStorage.clear();
    useEngineNav.setState({ conversationId: null, nonce: 0 });
    useProjectsStore.setState({ currentProjectId: null } as never);
    mockUnion.mockImplementation((enabled: boolean) => ({ data: enabled ? items : undefined }));
});

describe('ChatSidebar engine conversations', () => {
    it('opens an engine conversation with ?c= and flags the nav store', () => {
        mockMode.mockReturnValue('engine');
        render(<ChatSidebar />);
        fireEvent.click(screen.getByText('Engine chat'));
        expect(push).toHaveBeenCalledWith('/chat?c=eng1');
        expect(useEngineNav.getState().conversationId).toBe('eng1');
    });

    it('routes a project conversation to its project page with ?c=', () => {
        mockMode.mockReturnValue('engine');
        render(<ChatSidebar />);
        fireEvent.click(screen.getByText('Project engine chat'));
        expect(push).toHaveBeenCalledWith('/chat/projects/p9?c=eng2');
    });

    it('legacy items still select the legacy session', () => {
        mockMode.mockReturnValue('engine');
        render(<ChatSidebar />);
        fireEvent.click(screen.getByText('Legacy chat'));
        expect(useChatSessionsStore.getState().currentSessionId).toBe('leg1');
        expect(push).not.toHaveBeenCalled();
    });

    it('shows no engine items in legacy mode', () => {
        mockMode.mockReturnValue('legacy');
        render(<ChatSidebar />);
        expect(screen.queryByText('Engine chat')).toBeNull();
        expect(mockUnion).toHaveBeenCalledWith(false);
    });
});
