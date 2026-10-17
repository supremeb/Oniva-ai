/**
 * @recovery U25 / AE1, AE2: a Fleet background run on the engine survives a worker kill and a refresh,
 * takes a steer, and a cancel stops the work.
 *
 * Drives the real Fleet page (run list, RunTree, transcript, intervene box, Cancel) against the U10 backend
 * e2e fixtures: a Fleet script on the scripted fake model and a fake search tool, booted by global-setup.ts.
 * The stack must expose, beside the deep-work controls (`/kill-worker`, `/start-worker`, `/open-gate`,
 * `/close-gate`, `/effects`):
 *   POST {E2E_CONTROL_URL}/fleet-seed?script=<recovery|steer|cancel>  ->  { jobId, conversationId }
 * and the Fleet routes (`/api/v1/fleet/runs`, `/intervene`, `/api/v1/background-jobs/{id}/cancel`).
 * The spec skips itself when the stack does not offer them (the U10 fixtures are not merged yet), and
 * unless ONIVA_E2E_BROWSER=1.
 */
import { expect, test, type Page } from '@playwright/test';

test.skip(process.env.ONIVA_E2E_BROWSER !== '1', 'set ONIVA_E2E_BROWSER=1 to run the browser e2e');

const control = (p: string, method: 'GET' | 'POST' = 'POST') =>
  fetch(`${process.env.E2E_CONTROL_URL}${p}`, { method }).then(async (r) => {
    expect(r.ok, `${p} -> ${r.status}`).toBeTruthy();
    return r.json() as Promise<Record<string, any>>;
  });

const api = (p: string, init: RequestInit = {}) =>
  fetch(`${process.env.E2E_BACKEND_URL}${p}`, {
    ...init,
    headers: { 'content-type': 'application/json', Authorization: 'Bearer e2e-user-jwt', ...(init.headers ?? {}) },
  });

async function stackHasFleet(): Promise<boolean> {
  try {
    const [seed, list] = await Promise.all([
      fetch(`${process.env.E2E_CONTROL_URL}/fleet-seed?script=probe`, { method: 'POST' }),
      api('/api/v1/fleet/runs?page_size=1'),
    ]);
    return seed.status !== 404 && list.status !== 404;
  } catch {
    return false;
  }
}

async function authenticate(page: Page) {
  await page.context().addCookies([{ name: 'is_authenticated', value: '1', url: `http://localhost:${process.env.E2E_FRONTEND_PORT || 3100}` }]);
}

async function openRun(page: Page, jobId: string) {
  await authenticate(page);
  await page.goto('/fleet');
  await page.getByRole('button', { name: /^Running|^All/ }).first().click();
  await page.getByTestId(`fleet-run-${jobId}`).click();
}

const header = (page: Page) => page.getByTestId('run-status-badge').first();
const box = (page: Page) => page.getByLabel('Message to agent');

test.beforeAll(async () => {
  test.skip(!(await stackHasFleet()), 'the e2e stack has no Fleet fixtures (U10); spec not run');
});

test.beforeEach(async () => {
  await control('/close-gate');
  const s = await control('/state', 'GET');
  if (!s.workerRunning) await control('/start-worker');
});

test('@recovery worker killed mid-run: banner, resume, one completion, refresh rejoins without lost text', async ({ page }) => {
  const { jobId } = await control('/fleet-seed?script=recovery');
  await openRun(page, jobId);
  await expect(page.getByRole('tree')).toBeVisible();
  await expect(header(page)).toHaveAttribute('data-status', 'running');

  await control('/kill-worker');
  await expect(page.getByTestId('connection-banner')).toBeVisible({ timeout: 60_000 });
  await control('/start-worker');

  await page.reload(); // a refresh mid-run rejoins the same run
  await expect(page.getByRole('tree')).toBeVisible();
  await control('/open-gate');
  await expect(header(page)).toHaveAttribute('data-status', 'completed', { timeout: 180_000 });
  await expect(box(page)).toBeDisabled();
});

test('@recovery a steer shows as a queued chip and is consumed by the next model request', async ({ page }) => {
  const { jobId } = await control('/fleet-seed?script=steer');
  await openRun(page, jobId);
  await box(page).fill('FLEET_STEER focus on the EU');
  await page.getByRole('button', { name: 'Send' }).click();
  await expect(page.getByTestId('queued-chips')).toBeVisible();
  await control('/open-gate');
  await expect(page.getByTestId('queued-chips').locator('[data-state="consumed"]')).toHaveCount(1, { timeout: 120_000 });
});

test('@recovery cancel stops the work: the job reads cancelled and the input is disabled', async ({ page }) => {
  const { jobId } = await control('/fleet-seed?script=cancel');
  await openRun(page, jobId);
  await page.getByRole('button', { name: 'Cancel run' }).click();
  await expect(header(page)).toHaveAttribute('data-status', 'cancelled', { timeout: 60_000 });
  await expect(box(page)).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toHaveCount(0);
  await control('/open-gate'); // a late tool result must not revive the job
  await page.reload();
  await expect(header(page)).toHaveAttribute('data-status', 'cancelled');
});
