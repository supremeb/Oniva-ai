import { act, render, screen } from '@testing-library/react';
import { DeepWorkProgressBar } from '../DeepWorkProgressBar';
import { useDeepWorkStore } from '@/lib/stores/deep-work';
import { useEngineSessionStore } from '../engineSession';

const executing = () =>
  useDeepWorkStore.setState({
    status: 'executing',
    engineMode: true,
    enginePhase: 'researching',
    todos: [{ text: 'a', status: 'completed' }, { text: 'b', status: 'pending' }],
  });

beforeEach(() => {
  useDeepWorkStore.getState().reset();
  useEngineSessionStore.setState({ session: null, terminal: null });
});

describe('DeepWorkProgressBar', () => {
  it('shows legacy progress while a legacy run executes', () => {
    executing();
    render(<DeepWorkProgressBar />);
    expect(screen.getByText(/Deep Work — researching/)).toBeTruthy();
    expect(screen.getByText('1/2')).toBeTruthy();
  });

  it('is hidden for engine sessions: DeepWorkEngineCard owns progress there', () => {
    executing();
    render(<DeepWorkProgressBar />);
    act(() => {
      useEngineSessionStore.getState().setSession({ sessionId: 's', conversationId: 'c', chatId: 'chat', status: 'running', error: null, planTitle: null });
    });
    expect(screen.queryByText(/Deep Work —/)).toBeNull();
  });

  it('renders nothing for an engine session when the legacy store is idle', () => {
    useEngineSessionStore.getState().setSession({ sessionId: 's', conversationId: 'c', chatId: 'chat', status: 'running', error: null, planTitle: null });
    const { container } = render(<DeepWorkProgressBar />);
    expect(container.firstChild).toBeNull();
  });
});
