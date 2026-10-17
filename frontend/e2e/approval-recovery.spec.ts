/**
 * @recovery Approvals and policy U13: a pending approval survives a page reload and shows in the inbox.
 *
 * Drives the real Next.js app against the real backend + engine worker (scripted model) booted by
 * global-setup.ts. The scripted turn calls `execute_python`, an admin-level approval, so the chat user (a
 * member) cannot decide it. The card and the sidebar badge are there after a reload (the engine item and the
 * ledger row are durable); an admin then approves through the backend (the control API), the fake sandbox
 * runs the code once, and the model's answer arrives in the same tab. Skipped unless ONIVA_E2E_BROWSER=1.
 */
import { expect, test, type Page } from '@playwright/test';

const ANSWER = 'python finished (e2e-py-answer)';

test.skip(process.env.ONIVA_E2E_BROWSER !== '1', 'set ONIVA_E2E_BROWSER=1 to run the browser e2e');

const control = (p: string, method: 'GET' | 'POST' = 'POST') =>
  fetch(`${process.env.E2E_CONTROL_URL}${p}`, { method }).then(async (r) => {
    expect(r.ok, `${p} -> ${r.status}`).toBeTruthy();
    return r.json() as Promise<Record<string, any>>;
  });

async function openChat(page: Page) {
  await page.context().addCookies([{ name: 'is_authenticated', value: '1', url: `http://localhost:${process.env.E2E_FRONTEND_PORT || 3100}` }]);
  await page.goto('/chat');
  await expect(page.getByLabel('Message')).toBeEnabled();
}

const log = (page: Page) => page.getByTestId('engine-message-log');
const banner = (page: Page) => page.getByTestId('approval-banner');
const badge = (page: Page) => page.getByTestId('nav-badge');

test.beforeEach(async () => {
  const s = await control('/state', 'GET');
  if (!s.workerRunning) await control('/start-worker');
});

test('@recovery a pending execute_python approval survives a reload, shows in the inbox, and runs once after an admin approves', async ({ page }) => {
  const runsBefore = ((await control('/exec-log', 'GET')).runs as string[]).length;
  await openChat(page);

  const box = page.getByLabel('Message');
  await box.fill('RUN_PYTHON please');
  await box.press('Enter');

  // The call parks on approval: the banner is up, the card says why, the badge counts it.
  await expect(banner(page)).toBeVisible();
  await expect(log(page).getByText('execute_python', { exact: false }).first()).toBeVisible();
  await expect(badge(page)).toHaveText(/1/);
  await expect(page).toHaveURL(/[?&]c=/);

  // A reload rejoins the conversation: the approval is still pending, still counted.
  await page.reload();
  await expect(log(page).getByText('RUN_PYTHON please')).toBeVisible();
  await expect(banner(page)).toBeVisible();
  await expect(badge(page)).toHaveText(/1/);
  expect(((await control('/exec-log', 'GET')).runs as string[]).length).toBe(runsBefore); // nothing ran

  // The "Needs attention" page lists it for the requester, who cannot decide an admin-level tool.
  await page.goto('/inbox');
  const list = page.getByRole('list', { name: 'Needs attention' });
  await expect(list.getByText('execute_python', { exact: false }).first()).toBeVisible();
  await expect(list.getByRole('button', { name: /approve/i })).toHaveCount(0);

  // An admin approves (the backend decides under RBAC): the code runs once and the answer reaches the chat.
  const decided = await control('/decide-approval?who=admin&decision=approve');
  expect(decided.status).toBe(200);
  await page.goBack();
  await expect(log(page).getByText(ANSWER)).toBeVisible({ timeout: 120_000 });
  await expect(banner(page)).toHaveCount(0);
  const runs = (await control('/exec-log', 'GET')).runs as string[];
  expect(runs.length - runsBefore).toBe(1);

  // The inbox is empty again after a reload.
  await page.reload();
  await expect(log(page).getByText(ANSWER)).toBeVisible();
  await expect(badge(page)).toHaveCount(0);
});
