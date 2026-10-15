/** Skill picker + connector popover in the engine composer map to selections (create and mid-conversation PATCH). */
import { TextEncoder, TextDecoder } from 'util';
Object.assign(globalThis, { TextEncoder, TextDecoder });

import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { makeBody } from '@/lib/engine/__fixtures__/stream';
import type { EngineConversationClient } from '@/lib/engine';

jest.mock('react-markdown', () => ({ __esModule: true, default: ({ children }: { children?: React.ReactNode }) => <div>{children}</div> }));
jest.mock('remark-gfm', () => ({ __esModule: true, default: () => {} }));
jest.mock('@/components/ui/GenerativeO', () => ({ GenerativeO: () => <div />, GenerativeOText: () => <span /> }));
jest.mock('next/navigation', () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock('@/components/chat/ChatSelections', () => ({
    buildSelectionPopovers: () => ({ webSearchPopover: null, knowledgePopover: null }),
}));
jest.mock('@/components/chat/SkillPicker', () => ({
    SkillPicker: ({ onSkillSelect }: { onSkillSelect: (s: unknown) => void }) => (
        <button onClick={() => onSkillSelect({ id: 'sk1', name: 'Summarize' })}>pick-skill</button>
    ),
}));
jest.mock('@/components/chat/ConnectorPopover', () => ({
    ConnectorPopover: ({ onToggle, activeConnectorIds }: { onToggle: (id: string) => void; activeConnectorIds: string[] }) => (
        <button onClick={() => onToggle('cn1')}>toggle-connector:{activeConnectorIds.length}</button>
    ),
}));
jest.mock('@/lib/api', () => {
    class APIError extends Error {}
    return { API_BASE_URL: 'http://api.test', APIError, apiFetch: jest.fn(), attemptTokenRefresh: jest.fn(async () => true) };
});

import { EngineChat } from '../EngineChat';

function fakeClient() {
    const raw = {
        create: jest.fn(async () => ({ conversationId: 'conv-new', projectId: 'p1' })),
        watch: jest.fn(async (_id: string, _c: number | null, signal: AbortSignal) => ({ body: makeBody(signal).body }) as unknown as Response),
        submit: jest.fn(async () => ({})),
        abort: jest.fn(async () => ({})),
        decide: jest.fn(),
        updateSelections: jest.fn(async () => ({})),
    };
    return { raw, client: raw as unknown as EngineConversationClient };
}

beforeAll(() => {
    window.matchMedia ??= ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false })) as unknown as typeof window.matchMedia;
});
beforeEach(() => window.history.replaceState(null, '', '/chat'));

describe('engine composer skill + connector selections', () => {
    it('sends them on create, shows a removable skill tag', async () => {
        const f = fakeClient();
        render(<EngineChat projectId="p1" client={f.client} />);
        fireEvent.click(screen.getByText('pick-skill'));
        fireEvent.click(screen.getByText(/toggle-connector/));
        expect(screen.getByText('Summarize')).toBeTruthy();
        expect(f.raw.updateSelections).not.toHaveBeenCalled();

        fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), { target: { value: 'hi' } });
        await act(async () => {
            fireEvent.keyDown(screen.getByRole('textbox', { name: 'Message' }), { key: 'Enter' });
        });
        await waitFor(() => expect(f.raw.create).toHaveBeenCalled());
        expect(f.raw.create).toHaveBeenCalledWith('p1', expect.objectContaining({ skill_ids: ['sk1'], connector_ids: ['cn1'] }));
    });

    it('PATCHes selections mid-conversation when picking, toggling and removing', async () => {
        const f = fakeClient();
        render(<EngineChat projectId="p1" initialConversationId="c1" client={f.client} />);
        await waitFor(() => expect(f.raw.watch).toHaveBeenCalled());

        fireEvent.click(screen.getByText('pick-skill'));
        expect(f.raw.updateSelections).toHaveBeenLastCalledWith('c1', expect.objectContaining({ skill_ids: ['sk1'], connector_ids: [] }));
        fireEvent.click(screen.getByText(/toggle-connector/));
        expect(f.raw.updateSelections).toHaveBeenLastCalledWith('c1', expect.objectContaining({ skill_ids: ['sk1'], connector_ids: ['cn1'] }));
        fireEvent.click(screen.getByLabelText('Remove Summarize'));
        expect(f.raw.updateSelections).toHaveBeenLastCalledWith('c1', expect.objectContaining({ skill_ids: [], connector_ids: ['cn1'] }));
        expect(screen.queryByText('Summarize')).toBeNull();
    });
});
