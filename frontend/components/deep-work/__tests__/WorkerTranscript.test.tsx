import { render, screen } from '@testing-library/react';
import { WorkerTranscript } from '../WorkerTranscript';

const mockUse = jest.fn();
jest.mock('@/lib/engine/useEngineDeepWork', () => ({ useWorkerTranscript: (...a: unknown[]) => mockUse(...a) }));
jest.mock('@/lib/engine/viewToMessages', () => ({
  viewToMessages: () => [
    { id: 'm1', role: 'user', content: 'Find suppliers', isStreaming: false, entryIds: [], createdAt: 1 },
    { id: 'm2', role: 'assistant', content: 'Found three.', isStreaming: false, entryIds: [], createdAt: 2 },
  ],
}));

describe('WorkerTranscript', () => {
  it('watches the given child and renders its messages read-only', () => {
    mockUse.mockReturnValue({ view: {}, connection: 'live', error: null });
    render(<WorkerTranscript sessionId="dw_1" childConversationId="child_1" />);
    expect(mockUse).toHaveBeenCalledWith('dw_1', 'child_1');
    expect(screen.getByText('Find suppliers')).toBeInTheDocument();
    expect(screen.getByText('Found three.')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('shows loading and error states', () => {
    mockUse.mockReturnValue({ view: null, connection: 'connecting', error: null });
    const { rerender } = render(<WorkerTranscript sessionId="dw_1" childConversationId="child_1" />);
    expect(screen.getByText(/Loading transcript/)).toBeInTheDocument();
    mockUse.mockReturnValue({ view: null, connection: 'closed', error: 'forbidden' });
    rerender(<WorkerTranscript sessionId="dw_1" childConversationId="child_2" />);
    expect(screen.getByRole('alert')).toHaveTextContent('do not have access');
  });
});
