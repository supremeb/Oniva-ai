import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { NodePalette } from '@/components/workflow/NodePalette';
import { HumanInLoopNode } from '@/components/workflow/nodes/HumanInLoopNode';

jest.mock('@xyflow/react', () => ({
  Handle: () => null,
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
  useReactFlow: () => ({}),
  useNodeId: () => 'n1',
}));

describe('NodePalette', () => {
  it.each(['Data Retrieval', 'Analysis', 'Research', 'Schedule Trigger'])(
    'does not offer %s (no backend executor)',
    (label) => {
      render(<NodePalette />);
      expect(screen.queryByText(label)).not.toBeInTheDocument();
    }
  );

  it('still offers Human Approval', () => {
    render(<NodePalette />);
    expect(screen.getByText('Human Approval')).toBeInTheDocument();
  });
});

describe('HumanInLoopNode', () => {
  it('labels approvals as simulated', () => {
    render(<HumanInLoopNode {...({ id: 'n1', data: { title: 'Review' }, selected: false } as any)} />);
    expect(screen.getByText(/Simulated — approvals auto-approve/)).toBeInTheDocument();
  });
});
