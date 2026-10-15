/** Sidebar: engine items get rename / pin / delete (archive) wired to the engine endpoints. */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

jest.mock('next/navigation', () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock('@/lib/hooks/useFeature', () => ({ useEngineChatMode: () => 'engine' }));
jest.mock('@/hooks/useAgents', () => ({ useAgents: () => ({ data: [] }) }));
jest.mock('../AgentBrowserModal', () => ({ AgentBrowserModal: () => null }));
jest.mock('@/hooks/useChatSessions', () => ({
    useChatSessionsList: () => ({ data: [], isLoading: false }),
    useUpdateChatSession: () => ({ mutate: jest.fn() }),
    useDeleteChatSession: () => ({ mutate: jest.fn() }),
}));
const mockApiFetch = jest.fn();
jest.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => mockApiFetch(...a) }));

import { ChatSidebar } from '../ChatSidebar';
import { useProjectsStore } from '@/lib/stores/projects';

const response = (items: unknown[]) => ({ items, total: items.length, page: 1, page_size: 50 });
const eng = (over = {}) => ({ id: 'e/1', source: 'engine', title: 'Engine chat', updated_at: '2026-02-01T00:00:00Z', project_id: null, pinned: false, ...over });

function setup(initial: Array<Record<string, unknown>>) {
    // A tiny stateful server so the post-mutation refetch agrees with the optimistic update.
    let items = initial;
    mockApiFetch.mockReset();
    mockApiFetch.mockImplementation(async (_url: string, opts?: { method?: string; body?: string }) => {
        if (opts?.method === 'PATCH') items = items.map((i) => ({ ...i, ...JSON.parse(opts.body!) }));
        if (opts?.method === 'DELETE') items = [];
        return opts?.method ? {} : response(items);
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
        <QueryClientProvider client={qc}>
            <ChatSidebar />
        </QueryClientProvider>
    );
}

async function openMenu() {
    await screen.findByText('Engine chat');
    const trigger = document.querySelector('button[aria-haspopup="menu"]') as HTMLElement;
    fireEvent.keyDown(trigger, { key: 'Enter' });
}

const writes = () => mockApiFetch.mock.calls.filter((c) => c[1]?.method);

beforeEach(() => {
    localStorage.clear();
    useProjectsStore.setState({ currentProjectId: null, projects: [], loadProjects: jest.fn() } as never);
});

describe('engine item menu', () => {
    it('pin PATCHes pinned:true', async () => {
        setup([eng()]);
        await openMenu();
        fireEvent.click(await screen.findByText('Pin'));
        await waitFor(() => expect(writes()).toHaveLength(1));
        expect(writes()[0][0]).toBe('/api/v1/engine/conversations/e%2F1');
        expect(writes()[0][1]).toMatchObject({ method: 'PATCH', body: JSON.stringify({ pinned: true }) });
    });

    it('a pinned item shows in Pinned and offers Unpin', async () => {
        setup([eng({ pinned: true })]);
        expect(await screen.findByText('Pinned')).toBeTruthy();
        await openMenu();
        fireEvent.click(await screen.findByText('Unpin'));
        await waitFor(() => expect(writes()[0][1].body).toBe(JSON.stringify({ pinned: false })));
    });

    it('rename PATCHes the new title', async () => {
        setup([eng()]);
        await openMenu();
        fireEvent.click(await screen.findByText('Rename'));
        const input = (await screen.findByDisplayValue('Engine chat')) as HTMLInputElement;
        fireEvent.change(input, { target: { value: 'Renamed' } });
        fireEvent.keyDown(input, { key: 'Enter' });
        await waitFor(() => expect(writes()).toHaveLength(1));
        expect(writes()[0][1]).toMatchObject({ method: 'PATCH', body: JSON.stringify({ title: 'Renamed' }) });
        expect(await screen.findByText('Renamed')).toBeTruthy();
    });

    it('delete confirms, DELETEs, and removes the item optimistically', async () => {
        setup([eng()]);
        await openMenu();
        fireEvent.click(await screen.findByText('Delete'));
        expect(writes()).toHaveLength(0);
        fireEvent.click(screen.getByText('Delete this chat?').parentElement!.querySelector('button')!);
        await waitFor(() => expect(writes()).toHaveLength(1));
        expect(writes()[0][0]).toBe('/api/v1/engine/conversations/e%2F1');
        expect(writes()[0][1]).toMatchObject({ method: 'DELETE' });
        await waitFor(() => expect(screen.queryByText('Engine chat')).toBeNull());
    });
});
