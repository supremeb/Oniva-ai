import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import CompliancePage from '../page';
import { apiFetch } from '@/lib/api';

jest.mock('@/lib/api', () => ({ apiFetch: jest.fn() }));
const mocked = apiFetch as jest.Mock;

const stats = {
  total_audit_logs: 3,
  pending_reviews: 2,
  chain_integrity: { status: 'VALID', last_verified: new Date().toISOString(), issues_found: 0 },
  risk_distribution: {},
  recent_activity: { last_24h: 1, last_7d: 2, last_30d: 3 },
};

beforeEach(() => {
  mocked.mockReset();
  mocked.mockImplementation(async (url: string) => (String(url).includes('/compliance/stats') ? stats : []));
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CompliancePage />
    </QueryClientProvider>,
  );
}

test('links to the inbox review items instead of the old review queue', async () => {
  renderPage();
  const link = await screen.findByRole('link', { name: /review in inbox/i });
  expect(link).toHaveAttribute('href', '/inbox?kinds=review');
});

test('never calls the removed /audit/reviews routes', async () => {
  renderPage();
  await waitFor(() => expect(mocked).toHaveBeenCalled());
  const urls = mocked.mock.calls.map((c) => String(c[0]));
  expect(urls.some((u) => u.includes('/audit/reviews'))).toBe(false);
});
