import { act, fireEvent, render, screen } from '@testing-library/react';

jest.mock('../DeepWorkEngineCard', () => ({ DeepWorkEngineCard: () => <div data-testid="engine-card" /> }));
jest.mock('@/components/chat/shared/ChainOfThought', () => ({ ChainOfThought: () => null }));
jest.mock('@/components/chat/shared/data/FilePreview', () => ({ FilePreview: () => null }));
jest.mock('react-markdown', () => ({ __esModule: true, default: ({ children }: { children: string }) => <div>{children}</div> }));

import { DeepWorkCard } from '../DeepWorkCard';
import { useDeepWorkStore } from '@/lib/stores/deep-work';
import { useEngineSessionStore } from '../engineSession';

beforeEach(() => {
  useDeepWorkStore.getState().reset();
  useEngineSessionStore.setState({ session: null, terminal: null });
});

describe('DeepWorkCard on the engine intake', () => {
  it('plan 422: shows the message and the model text, and Retry replans', () => {
    const generatePlan = jest.fn();
    useDeepWorkStore.setState({
      status: 'error',
      engineMode: true,
      error: 'The plan could not be parsed',
      planFailure: { message: 'The plan could not be parsed', text: 'not json at all' },
      enginePendingQuery: 'battery market',
      generatePlan,
    });
    render(<DeepWorkCard />);
    expect(screen.getByText('The plan could not be parsed')).toBeTruthy();
    expect(screen.getByText('not json at all')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(generatePlan).toHaveBeenCalledWith('battery market');
  });

  it('mounts the engine card once the start publishes a session, and the intake card gives way', () => {
    useDeepWorkStore.setState({ status: 'error', engineMode: true, error: 'x' });
    render(<DeepWorkCard />);
    expect(screen.queryByTestId('engine-card')).toBeNull();
    act(() => {
      useEngineSessionStore.getState().setSession({ sessionId: 's', conversationId: 'c', chatId: 'chat', status: 'running', error: null, planTitle: 't' });
    });
    expect(screen.getByTestId('engine-card')).toBeTruthy();
  });
});
