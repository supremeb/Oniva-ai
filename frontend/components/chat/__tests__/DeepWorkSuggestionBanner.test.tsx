import { render, screen, fireEvent } from '@testing-library/react';
import { DeepWorkSuggestionBanner } from '../DeepWorkSuggestionBanner';

describe('DeepWorkSuggestionBanner', () => {
  const defaultProps = {
    reasoning: 'This query requires multi-step research across SEC filings and market data.',
    onAccept: jest.fn(),
    onDismiss: jest.fn(),
  };

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders the suggestion message', () => {
    render(<DeepWorkSuggestionBanner {...defaultProps} />);

    expect(screen.getByText('This query looks like it needs deep research')).toBeInTheDocument();
    expect(screen.getByText(defaultProps.reasoning)).toBeInTheDocument();
  });

  it('renders accept and dismiss buttons', () => {
    render(<DeepWorkSuggestionBanner {...defaultProps} />);

    expect(screen.getByText('Switch to Deep Work')).toBeInTheDocument();
    expect(screen.getByText('Continue as chat')).toBeInTheDocument();
  });

  it('calls onAccept when Switch to Deep Work is clicked', () => {
    render(<DeepWorkSuggestionBanner {...defaultProps} />);

    fireEvent.click(screen.getByText('Switch to Deep Work'));
    expect(defaultProps.onAccept).toHaveBeenCalledTimes(1);
    expect(defaultProps.onDismiss).not.toHaveBeenCalled();
  });

  it('calls onDismiss when Continue as chat is clicked', () => {
    render(<DeepWorkSuggestionBanner {...defaultProps} />);

    fireEvent.click(screen.getByText('Continue as chat'));
    expect(defaultProps.onDismiss).toHaveBeenCalledTimes(1);
    expect(defaultProps.onAccept).not.toHaveBeenCalled();
  });

  it('calls onDismiss when X button is clicked', () => {
    render(<DeepWorkSuggestionBanner {...defaultProps} />);

    // The X button is the last button-like element
    const buttons = screen.getAllByRole('button');
    const closeButton = buttons[buttons.length - 1];
    fireEvent.click(closeButton);

    expect(defaultProps.onDismiss).toHaveBeenCalledTimes(1);
  });

  it('renders with empty reasoning gracefully', () => {
    render(<DeepWorkSuggestionBanner {...defaultProps} reasoning="" />);

    expect(screen.getByText('This query looks like it needs deep research')).toBeInTheDocument();
    expect(screen.getByText('Switch to Deep Work')).toBeInTheDocument();
  });
});
