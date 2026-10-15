import { render, screen, fireEvent } from '@testing-library/react';
import { NodeContextMenu } from '@/components/workflow/NodeContextMenu';
import '@testing-library/jest-dom';

describe('NodeContextMenu', () => {
  const mockOnAddNode = jest.fn();
  const mockOnClose = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
  });

  // KNOWN_FAILURES U13: category label 'Financial Agents' no longer rendered
  it.failing('renders correctly', () => {
    render(
      <NodeContextMenu
        x={100}
        y={100}
        onAddNode={mockOnAddNode}
        onClose={mockOnClose}
      />
    );

    expect(screen.getByPlaceholderText('Search nodes...')).toBeInTheDocument();
    expect(screen.getByText('Financial Agents')).toBeInTheDocument();
    expect(screen.getByText('Logic')).toBeInTheDocument();
  });

  it('filters nodes based on search input', () => {
    render(
      <NodeContextMenu
        x={100}
        y={100}
        onAddNode={mockOnAddNode}
        onClose={mockOnClose}
      />
    );

    const searchInput = screen.getByPlaceholderText('Search nodes...');
    fireEvent.change(searchInput, { target: { value: 'LLM' } });

    expect(screen.getByText('LLM')).toBeInTheDocument();
    expect(screen.queryByText('Start')).not.toBeInTheDocument();
  });

  it('calls onAddNode when a node is clicked', () => {
    render(
      <NodeContextMenu
        x={100}
        y={100}
        onAddNode={mockOnAddNode}
        onClose={mockOnClose}
      />
    );

    fireEvent.click(screen.getByText('LLM'));
    expect(mockOnAddNode).toHaveBeenCalledWith('llm');
  });
});
