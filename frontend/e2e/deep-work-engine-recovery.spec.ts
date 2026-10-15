/**
 * @recovery U13 / AE3, AE4, AE8, AE10: Deep Work on the engine survives a worker kill and a refresh, and takes a steer.
 *
 * Drives the real Next.js app (engine chat, Deep Work toggle, plan card, engine card) against the real backend
 * routes and the real engine worker with a scripted model, booted by global-setup.ts. Search is a fake that
 * writes a log and, for a query containing GATED, blocks on a gate the test controls; the "restart" scenario
 * holds the orchestrator's own non-replayable write (`e2e_hold`) on the chat gate instead. Scripts:
 * backend/tests/e2e/dw_script.py. Skipped unless ONIVA_E2E_BROWSER=1.
 *
 * What the card can show after a kill: a killed read (the worker's search) is simply replayed, so the root
 * conversation never learns of the restart and there is no "resumed" marker; a killed non-replayable write
 * in the orchestrator's own round leaves an `interrupted` result, which is the marker. Both are covered.
 */
import { expect, test, type Page } from '@playwright/test';

test.skip(process.env.ONIVA_E2E_BROWSER !== '1', 'set ONIVA_E2E_BROWSER=1 to run the browser e2e');

const QUERY = {
  restart: 'DWQ_RESTART compare the two vendors',
  recovery: 'DWQ_RECOVERY compare the two vendors',
  steer: 'DWQ_STEER compare the two vendors',
} as const;
const TITLE = { restart: 'DWPLAN_RESTART', recovery: 'DWPLAN_RECOVERY', steer: 'DWPLAN_STEER' } as const;
type Scenario = keyof typeof QUERY;
// The card shows the written sections; the `## Sources` block is appended to the saved report.md by dw_finalize.
const SECTIONS = ['Findings', 'Method'];

const control = (p: string, method: 'GET' | 'POST' = 'POST') =>
  fetch(`${process.env.E2E_CONTROL_URL}${p}`, { method }).then(async (r) => {
    expect(r.ok, `${p} -> ${r.status}`).toBeTruthy();
    return r.json() as Promise<Record<string, any>>;
  });
const effects = async () => (await control('/effects', 'GET')).effects as string[];
const dwState = async () => (await control('/dw-state', 'GET')) as { searches: string[]; sandbox: string[]; inFlight: string[] };

async function newConversation(): Promise<string> {
  const r = await fetch(`${process.env.E2E_BACKEND_URL}/api/v1/engine/conversations`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', Authorization: 'Bearer e2e-user-jwt' },
    body: JSON.stringify({ project_id: process.env.E2E_PROJECT_ID, selections: {} }),
  });
  expect(r.status, await r.clone().text()).toBe(201);
  return ((await r.json()) as { conversationId: string }).conversationId;
}

async function authenticate(page: Page) {
  await page.context().addCookies([{ name: 'is_authenticated', value: '1', url: `http://localhost:${process.env.E2E_FRONTEND_PORT || 3100}` }]);
}

/** Opens a fresh engine chat, turns Deep Work on, asks the scenario's question and approves the plan. */
async function startDeepWork(page: Page, scenario: Scenario) {
  const conversationId = await newConversation();
  await authenticate(page);
  await page.goto(`/chat?c=${conversationId}`);
  const box = page.getByLabel('Message');
  await expect(box).toBeEnabled();
  await page.getByRole('button', { name: 'Deep Work' }).click();
  await box.fill(QUERY[scenario]);
  await box.press('Enter');
  await expect(page.getByText(TITLE[scenario])).toBeVisible(); // the plan card
  await page.getByRole('button', { name: 'Proceed' }).click();
  await expect(card(page)).toBeVisible();
  return conversationId;
}

const card = (page: Page) => page.getByTestId('deep-work-engine-card');
const rows = (page: Page) => page.getByTestId('worker-row');
const banner = (page: Page) => card(page).getByTestId('connection-banner'); // the chat has its own banner too
const report = (page: Page) => page.getByTestId('deep-work-report');

const inFlight = (tool: string) => async () => (await dwState()).inFlight.includes(tool);
/** The fake search log is shared by the tests of this run: only lines after `since` belong to the test at hand. */
const searchRunning = (query: string, since: number) => async () => {
  const searches = (await dwState()).searches.slice(since);
  return searches.includes(`start ${query}`) && !searches.includes(`done ${query}`);
};
const searchLogLength = async () => (await dwState()).searches.length;
async function until(what: string, fn: () => Promise<boolean>, timeoutMs = 90_000) {
  await expect.poll(fn, { message: what, timeout: timeoutMs, intervals: [300] }).toBe(true);
}

/** Counts the calls that start a run, so a refresh or a second tab is shown not to start another. */
function countStarts(page: Page) {
  const starts: string[] = [];
  page.on('request', (r) => r.method() === 'POST' && /\/api\/v1\/deep-work\/engine$/.test(r.url()) && starts.push(r.url()));
  return starts;
}

test.beforeEach(async () => {
  await control('/close-gate');
  await control('/dw-close-gate');
  const s = await control('/state', 'GET');
  if (!s.workerRunning) await control('/start-worker');
});

test('@recovery worker killed while the orchestrator\'s write is in flight: banner, resumed chip, one run without duplicates', async ({ page }) => {
  const starts = countStarts(page);
  const effectsBefore = (await effects()).length;
  await startDeepWork(page, 'restart');
  await expect(rows(page)).toHaveCount(2);
  await until('the orchestrator\'s write to be in flight', inFlight('e2e_hold'));

  await control('/kill-worker');
  await expect(banner(page)).toBeVisible({ timeout: 60_000 });
  await expect(banner(page)).toHaveAttribute('data-connection', 'reconnecting');

  await control('/start-worker'); // gate still closed: the engine cannot know whether the write happened
  await expect(page.getByTestId('resumed-chip')).toBeVisible({ timeout: 120_000 });
  await expect(card(page)).toHaveAttribute('data-status', 'completed', { timeout: 180_000 });
  await expect(banner(page)).toHaveCount(0);

  // The held write finishes once in the backend; the report has each section once; two workers, one start.
  await control('/open-gate');
  await until('the held write to finish', async () => (await effects()).length - effectsBefore === 1, 30_000);
  await expect(rows(page)).toHaveCount(2);
  await expect(report(page).locator('h2')).toHaveText(SECTIONS);
  expect(starts).toHaveLength(1);
  expect((await effects()).length - effectsBefore).toBe(1);
});

test('@recovery worker killed mid-search: banner, then the same card completes with no duplicate worker or section', async ({ page }) => {
  const starts = countStarts(page);
  const before = await searchLogLength();
  await startDeepWork(page, 'recovery');
  await expect(rows(page)).toHaveCount(2);
  await until('worker 2 mid-search', searchRunning('q-rec-w2-GATED', before));
  await expect(rows(page).filter({ hasText: 'q-rec-w2-GATED' })).toHaveAttribute('data-status', 'researching');

  await control('/kill-worker');
  await expect(banner(page)).toBeVisible({ timeout: 60_000 });
  await expect(banner(page)).toHaveAttribute('data-connection', 'reconnecting');

  await control('/start-worker');
  await control('/dw-open-gate');
  await expect(card(page)).toHaveAttribute('data-status', 'completed', { timeout: 180_000 });
  await expect(banner(page)).toHaveCount(0);

  await expect(rows(page)).toHaveCount(2);
  await expect(report(page).locator('h2')).toHaveText(SECTIONS);
  expect(starts).toHaveLength(1);
  const log = (await dwState()).searches.slice(before);
  expect(log.filter((l) => l === 'start q-rec-w1')).toHaveLength(1);
  expect(log.filter((l) => l === 'start q-rec-w2-GATED')).toHaveLength(1);
});

test('@recovery refresh mid-research rejoins without a new start and a second tab shows the same panel', async ({ page }) => {
  const starts = countStarts(page);
  const before = await searchLogLength();
  const conversationId = await startDeepWork(page, 'recovery');
  await expect(rows(page)).toHaveCount(2);
  await until('worker 2 mid-search', searchRunning('q-rec-w2-GATED', before));
  await expect(rows(page).filter({ hasText: 'q-rec-w1' })).toHaveAttribute('data-status', 'complete', { timeout: 60_000 });
  expect(starts).toHaveLength(1);

  // Refresh: the plan, one finished worker with findings, one running worker, no new submit.
  await page.reload();
  await expect(card(page)).toBeVisible({ timeout: 60_000 });
  await expect(card(page)).toContainText(TITLE.recovery);
  await expect(rows(page)).toHaveCount(2);
  await expect(rows(page).filter({ hasText: 'q-rec-w1' })).toHaveAttribute('data-status', 'complete');
  await expect(rows(page).filter({ hasText: 'q-rec-w2-GATED' })).toHaveAttribute('data-status', 'researching');
  expect(starts).toHaveLength(1);

  // A second tab converges on the same view.
  const other = await page.context().newPage();
  const otherStarts = countStarts(other);
  await other.goto(`/chat?c=${conversationId}`);
  await expect(card(other)).toBeVisible({ timeout: 60_000 });
  await expect(rows(other)).toHaveCount(2);
  await expect(rows(other).filter({ hasText: 'q-rec-w1' })).toHaveAttribute('data-status', 'complete');
  await expect(rows(other).filter({ hasText: 'q-rec-w2-GATED' })).toHaveAttribute('data-status', 'researching');
  expect(otherStarts).toHaveLength(0);

  await control('/dw-open-gate');
  await expect(card(page)).toHaveAttribute('data-status', 'completed', { timeout: 180_000 });
  await expect(card(other)).toHaveAttribute('data-status', 'completed', { timeout: 60_000 });
  await expect(rows(page)).toHaveCount(2);
  await expect(rows(other)).toHaveCount(2);
  await expect(report(page).locator('h2')).toHaveText(SECTIONS);
  await expect(report(other).locator('h2')).toHaveText(SECTIONS);
  expect(starts).toHaveLength(1);
});

test('@recovery steer during research shows one chip that is consumed after the tool round', async ({ page }) => {
  const before = await searchLogLength();
  await startDeepWork(page, 'steer');
  await expect(rows(page)).toHaveCount(2);
  await until('worker 2 mid-search', searchRunning('q-st-w2-GATED', before));

  await page.getByLabel('Steer the research').fill('DW_REDIRECT focus on pricing');
  await card(page).getByRole('button', { name: 'Send', exact: true }).click();
  const chips = page.getByTestId('queued-chips').locator('li');
  await expect(chips).toHaveCount(1);
  await expect(chips.first()).toHaveAttribute('data-mode', 'steer');

  await control('/dw-open-gate'); // the round ends; the steer is consumed after it
  await expect(card(page)).toHaveAttribute('data-status', 'completed', { timeout: 180_000 });
  await expect(page.getByTestId('queued-chips').locator('li[data-state="queued"]')).toHaveCount(0);
});
