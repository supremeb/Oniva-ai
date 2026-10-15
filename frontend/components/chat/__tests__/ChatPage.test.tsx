import React from 'react';
import { render, screen } from '@testing-library/react';

const mockMode = jest.fn();
jest.mock('@/lib/hooks/useFeature', () => ({ useEngineChatMode: () => mockMode() }));
jest.mock('../EngineChat', () => ({
    EngineChat: ({ initialConversationId }: { initialConversationId: string | null }) => <div data-testid="engine" data-c={initialConversationId ?? ''} />,
}));
jest.mock('../LegacyChat', () => ({ LegacyChat: () => <div data-testid="legacy" /> }));

import { ChatPage } from '../ChatPage';

describe('ChatPage mode', () => {
    afterEach(() => window.history.replaceState(null, '', '/'));

    it('renders legacy chat when the tenant flag is off', () => {
        mockMode.mockReturnValue('legacy');
        render(<ChatPage />);
        expect(screen.getByTestId('legacy')).toBeTruthy();
        expect(screen.queryByTestId('engine')).toBeNull();
    });

    it('renders engine chat when on, rejoining the conversation in ?c=', () => {
        mockMode.mockReturnValue('engine');
        window.history.replaceState(null, '', '/chat?c=conv-9');
        render(<ChatPage />);
        expect(screen.getByTestId('engine').getAttribute('data-c')).toBe('conv-9');
        expect(screen.queryByTestId('legacy')).toBeNull();
    });
});
