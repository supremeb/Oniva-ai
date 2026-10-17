import { fireEvent, render, screen, within } from '@testing-library/react';
import { RUN_STATUSES, runStatusLabel } from '@/lib/run/status';
import type { RunNode } from '@/lib/run/tree';
import { RunTree } from '../RunTree';
import { RunStatusBadge } from '../RunStatusBadge';

const node = (over: Partial<RunNode> & { id: string }): RunNode => ({
  kind: 'node',
  label: over.id,
  status: 'running',
  startedAt: null,
  endedAt: null,
  children: [],
  ...over,
});

describe('RunTree', () => {
  it('renders each of the nine statuses with a distinct label and accessible name', () => {
    const nodes = RUN_STATUSES.map((s) => node({ id: `n_${s}`, label: `node ${s}`, status: s }));
    render(<RunTree nodes={nodes} />);
    const labels = new Set<string>();
    for (const s of RUN_STATUSES) {
      const item = screen.getByRole('treeitem', { name: new RegExp(`node ${s}`) });
      expect(item).toHaveAttribute('data-status', s);
      expect(within(item).getByText(runStatusLabel(s))).toBeInTheDocument();
      expect(item.getAttribute('aria-label')).toContain(runStatusLabel(s));
      labels.add(runStatusLabel(s));
    }
    expect(labels.size).toBe(9);
    expect(screen.getByRole('tree')).toBeInTheDocument();
  });

  it('badge exposes the status label', () => {
    render(<RunStatusBadge status="skipped" />);
    expect(screen.getByText('Skipped')).toBeInTheDocument();
  });

  it('expanding a node shows its children', () => {
    const tree = [node({ id: 'root', label: 'Root', children: [node({ id: 'a', label: 'Alpha', children: [node({ id: 'b', label: 'Beta', children: [node({ id: 'c', label: 'Gamma' })] })] })] })];
    render(<RunTree nodes={tree} />);
    // depth 0 and 1 expanded, depth 2 collapsed by default
    expect(screen.getByText('Beta')).toBeInTheDocument();
    expect(screen.queryByText('Gamma')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /expand beta/i }));
    expect(screen.getByText('Gamma')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /collapse beta/i }));
    expect(screen.queryByText('Gamma')).not.toBeInTheDocument();
  });

  it('renders the first 20 of 50 children with a show more control', () => {
    const kids = Array.from({ length: 50 }, (_, i) => node({ id: `k${i}`, label: `kid-${i}` }));
    render(<RunTree nodes={[node({ id: 'root', label: 'Root', children: kids })]} />);
    expect(screen.getByText('kid-19')).toBeInTheDocument();
    expect(screen.queryByText('kid-20')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /show more/i }));
    expect(screen.getByText('kid-39')).toBeInTheDocument();
    expect(screen.queryByText('kid-40')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /show more/i }));
    expect(screen.getByText('kid-49')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /show more/i })).not.toBeInTheDocument();
  });

  it('explains resuming and interrupted', () => {
    render(<RunTree nodes={[node({ id: 'r', label: 'R', status: 'resuming' }), node({ id: 'i', label: 'I', status: 'interrupted' })]} />);
    expect(screen.getByText(/picking up where it left off/i)).toBeInTheDocument();
    expect(screen.getByText(/outcome is unknown/i)).toBeInTheDocument();
  });

  it('shows the wake time of a waiting row, elapsed time, usage and error', () => {
    const waitUntil = new Date(Date.UTC(2026, 9, 17, 12, 30)).toISOString();
    render(
      <RunTree
        now={Date.parse('2026-10-17T12:02:05Z')}
        nodes={[
          node({ id: 'w', label: 'Sleeper', status: 'waiting', startedAt: '2026-10-17T12:00:00Z', waitUntil }),
          node({ id: 'u', label: 'Costly', status: 'completed', startedAt: '2026-10-17T12:00:00Z', endedAt: '2026-10-17T12:00:42Z', usage: { totalTokens: 1530 } }),
          node({ id: 'e', label: 'Broken', status: 'failed', error: 'boom' }),
        ]}
      />,
    );
    expect(screen.getByTestId('wake-w')).toHaveTextContent(/wakes/i);
    expect(screen.getByTestId('wake-w').getAttribute('datetime')).toBe(waitUntil);
    expect(screen.getByText('2m 5s')).toBeInTheDocument();
    expect(screen.getByText('42s')).toBeInTheDocument();
    expect(screen.getByText('1.5k tokens')).toBeInTheDocument();
    expect(screen.getByText('boom')).toBeInTheDocument();
  });

  it('is navigable with the keyboard', () => {
    const tree = [node({ id: 'root', label: 'Root', children: [node({ id: 'a', label: 'Alpha', children: [node({ id: 'b', label: 'Beta', children: [node({ id: 'c', label: 'Gamma' })] })] }), node({ id: 'z', label: 'Zed' })] })];
    render(<RunTree nodes={tree} />);
    const item = (name: string) => screen.getByRole('treeitem', { name: new RegExp(name) });
    expect(item('Root')).toHaveAttribute('tabindex', '0');
    expect(item('Alpha')).toHaveAttribute('tabindex', '-1');
    item('Root').focus();
    fireEvent.keyDown(item('Root'), { key: 'ArrowDown' });
    expect(item('Alpha')).toHaveFocus();
    fireEvent.keyDown(item('Alpha'), { key: 'ArrowDown' });
    expect(item('Beta')).toHaveFocus();
    // Beta is collapsed: ArrowRight expands, ArrowRight again moves to first child
    fireEvent.keyDown(item('Beta'), { key: 'ArrowRight' });
    expect(item('Beta')).toHaveAttribute('aria-expanded', 'true');
    fireEvent.keyDown(item('Beta'), { key: 'ArrowRight' });
    expect(item('Gamma')).toHaveFocus();
    // ArrowLeft on a leaf goes to its parent; on an expanded parent it collapses
    fireEvent.keyDown(item('Gamma'), { key: 'ArrowLeft' });
    expect(item('Beta')).toHaveFocus();
    fireEvent.keyDown(item('Beta'), { key: 'ArrowLeft' });
    expect(item('Beta')).toHaveAttribute('aria-expanded', 'false');
    fireEvent.keyDown(item('Beta'), { key: 'ArrowUp' });
    expect(item('Alpha')).toHaveFocus();
    fireEvent.keyDown(item('Alpha'), { key: 'End' });
    expect(item('Zed')).toHaveFocus();
    fireEvent.keyDown(item('Zed'), { key: 'Home' });
    expect(item('Root')).toHaveFocus();
    // Enter toggles
    fireEvent.keyDown(item('Root'), { key: 'Enter' });
    expect(item('Root')).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Alpha')).not.toBeInTheDocument();
  });

  it('keeps animation reduced-motion safe', () => {
    const { container } = render(<RunTree nodes={[node({ id: 'x', status: 'running' })]} />);
    const spinning = container.querySelectorAll('.animate-spin');
    expect(spinning.length).toBeGreaterThan(0);
    spinning.forEach((el) => expect(el.getAttribute('class')).toContain('motion-reduce:animate-none'));
  });

  it('renders an empty state without throwing', () => {
    render(<RunTree nodes={[]} />);
    expect(screen.getByRole('tree')).toBeInTheDocument();
  });
});
