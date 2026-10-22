/**
 * @recovery U12 / AE10, AE3, AE5: chat on the engine survives a worker kill, a refresh, and takes a steer.
 *
 * Drives the real Next.js app against the real backend + engine worker (scripted model) booted by
 * global-setup.ts. The scripted turn streams a line of text, then calls a write tool that blocks on a
 * gate the test controls. Skipped unless ONIVA_E2E_BROWSER=1.
 */
import { expect, test, type Page } from '@playwright/test';

const PRE_TEXT = 'Saving the note now (e2e-pre-text)';
const ANSWER = 'write finished (e2e-answer-2)';
const STEER_ANSWER = 'steer received (e2e-steer)';

test.skip(process.env.ONIVA_E2E_BROWSER !== '1', 'set ONIVA_E2E_BROWSER=1 to run the browser e2e');

const control = (p: string, method: 'GET' | 'POST' = 'POST') =>
  fetch(`${process.env.E2E_CONTROL_URL}${p}`, { method }).then(async (r) => {
    expect(r.ok, `${p} -> ${r.status}`).toBeTruthy();
    return r.json() as Promise<Record<string, any>>;
  });
const effects = async () => (await control('/effects', 'GET')).effects as string[];

async function openChat(page: Page) {
  await page.context().addCookies([{ name: 'is_authenticated', value: '1', url: page.context().browser() ? `http://localhost:${process.env.E2E_FRONTEND_PORT || 3100}` : undefined }]);
  await page.goto('/chat');
  await expect(page.getByLabel('Message')).toBeEnabled();
}

async function send(page: Page, text: string) {
  const box = page.getByLabel('Message');
  await box.fill(text);
  await box.press('Enter');
}

const log = (page: Page) => page.getByTestId('engine-message-log');
const banner = (page: Page) => page.getByTestId('connection-banner');

test.beforeEach(async () => {
  await control('/close-gate');
  const s = await control('/state', 'GET');
  if (!s.workerRunning) await control('/start-worker');
});

test('@recovery worker killed mid-tool: banner, then the run completes in the same tab without duplicates', async ({ page }) => {
  const before = (await effects()).length;
  const submissions: string[] = [];
  page.on('request', (r) => r.method() === 'POST' && /\/submissions$/.test(r.url()) && submissions.push(r.url()));
  await openChat(page);

  await send(page, 'RUN_CHAT_WRITE please');
  await expect(log(page).getByText(PRE_TEXT)).toBeVisible();
  await expect(log(page).getByText('E2e Write')).toBeVisible(); // the tool is in flight (gate closed)

  await control('/kill-worker');
  await expect(banner(page)).toBeVisible({ timeout: 60_000 });
  await expect(banner(page)).toHaveAttribute('data-connection', 'reconnecting');

  await control('/open-gate'); // the backend-side write finishes without the dead caller
  // Wait for the write itself (deterministic, bounded) before the worker returns: the continuation can only
  // see a stored outcome, so a missing effect here means the write never ran, not that it is late.
  await expect.poll(async () => (await effects()).length - before, { timeout: 30_000, message: 'the held write runs once the gate opens' }).toBe(1);
  await control('/start-worker');
  await expect(log(page).getByText(ANSWER)).toBeVisible({ timeout: 120_000 });
  await expect(banner(page)).toHaveCount(0);

  // Same tab, no duplicate text, one user message, no re-submit, exactly one tool effect.
  await expect(log(page).getByText(PRE_TEXT)).toHaveCount(1);
  await expect(log(page).getByText(ANSWER)).toHaveCount(1);
  await expect(log(page).getByText('RUN_CHAT_WRITE please')).toHaveCount(1);
  expect(submissions).toHaveLength(1);
  // The run has settled: the effect count is final. A re-run of the write after the restart would make it 2.
  expect((await effects()).length - before).toBe(1);
});

test('@recovery refresh mid-run rejoins without a new submit', async ({ page }) => {
  const submissions: string[] = [];
  page.on('request', (r) => r.method() === 'POST' && /\/submissions$/.test(r.url()) && submissions.push(r.url()));
  const before = (await effects()).length;
  await openChat(page);
  await send(page, 'RUN_CHAT_WRITE refresh');
  await expect(log(page).getByText(PRE_TEXT)).toBeVisible();
  expect(submissions).toHaveLength(1);
  await expect(page).toHaveURL(/[?&]c=/);

  await page.reload();
  await expect(log(page).getByText('RUN_CHAT_WRITE refresh')).toBeVisible(); // committed user entry
  await expect(log(page).getByText(PRE_TEXT)).toBeVisible(); // partial assistant text
  expect(submissions).toHaveLength(1); // the reload did not submit again

  await control('/open-gate');
  await expect(log(page).getByText(ANSWER)).toBeVisible({ timeout: 60_000 });
  await expect(log(page).getByText(PRE_TEXT)).toHaveCount(1);
  expect((await effects()).length - before).toBe(1);
});

test('@recovery steer during the run shows one chip that is consumed after the tool round', async ({ page }) => {
  await openChat(page);
  await send(page, 'RUN_CHAT_WRITE steer');
  await expect(log(page).getByText(PRE_TEXT)).toBeVisible();

  await page.getByLabel('Message').fill('STEER_NOW please');
  await page.getByRole('button', { name: 'Steer now' }).click();
  const chips = page.getByTestId('queued-chips').locator('li');
  await expect(chips).toHaveCount(1);
  await expect(chips.first()).toHaveAttribute('data-mode', 'steer');

  await control('/open-gate');
  await expect(log(page).getByText(STEER_ANSWER)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId('queued-chips').locator('li[data-state="queued"]')).toHaveCount(0);
});
