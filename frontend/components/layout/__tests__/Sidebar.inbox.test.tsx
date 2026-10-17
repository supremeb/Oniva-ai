import { render, screen } from '@testing-library/react';
import { Sidebar } from '../Sidebar';
import { useInboxCount } from '@/hooks/useInbox';

jest.mock('next/navigation', () => ({ usePathname: () => '/chat' }));
jest.mock('@/lib/hooks/useFeature', () => ({ useFeatures: () => ({}) }));
jest.mock('@/hooks/useInbox');
jest.mock('@/components/layout/UserProfileMenu', () => ({ UserProfileMenu: () => null }));
jest.mock('@/components/ui/GenerativeO', () => ({ GenerativeO: () => null }));
const mocked = useInboxCount as jest.Mock;

test('link to /inbox sits under Chat', () => {
  mocked.mockReturnValue(0);
  render(<Sidebar />);
  const hrefs = screen.getAllByRole('link').map((l) => l.getAttribute('href'));
  expect(hrefs.indexOf('/inbox')).toBe(hrefs.indexOf('/chat') + 1);
});

test('badge hides at zero', () => {
  mocked.mockReturnValue(0);
  render(<Sidebar />);
  expect(screen.queryByTestId('nav-badge')).not.toBeInTheDocument();
});

test('badge shows count with accessible text', () => {
  mocked.mockReturnValue(3);
  render(<Sidebar />);
  expect(screen.getByTestId('nav-badge')).toHaveTextContent('3');
  expect(screen.getByText('3 items need attention')).toBeInTheDocument();
});

test('badge caps at 99+', () => {
  mocked.mockReturnValue(100);
  render(<Sidebar />);
  expect(screen.getByText('99+')).toBeInTheDocument();
});
