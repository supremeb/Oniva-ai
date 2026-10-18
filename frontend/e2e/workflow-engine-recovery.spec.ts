/**
 * @recovery U26 / AE1, AE2, AE5: a workflow run on the engine is followed as a run tree in the Logs panel, survives
 * a worker kill and a refresh, parks on a human decision with its wake time, and finishes from the stored trace.
 *
 * Drives the real workflow page (Logs tab, log detail, RunTree, Approve/Reject) against the backend e2e stack with
 * the workflow fixtures from U22 (backend/tests/e2e/workflow_helpers.py): `POST {E2E_BACKEND_URL}/e2e/workflows`
 * saves a workflow, `POST /api/v1/workflows/{id}/run` starts it on the engine, `GET /e2e/http/hold/<tag>` is the
 * counting HTTP target that waits for the gate, and the control server offers `/kill-worker`, `/start-worker`,
 * `/open-gate`, `/close-gate` and `/decide-approval?who=admin&decision=approve`.
 * The spec skips itself when the stack does not offer the workflow fixtures, and unless ONIVA_E2E_BROWSER=1.
 * The e2e user's role (analyst) may not decide approvals, so the in-page Approve ends in the Phase 5 "you cannot
 * decide this" state and the admin decision comes through the control server (the inbox path).
 */
import { expect, test, type Page } from '@playwright/test';

test.skip(process.env.ONIVA_E2E_BROWSER !== '1', 'set ONIVA_E2E_BROWSER=1 to run the browser e2e');

const control = (p: string, method: 'GET' | 'POST' = 'POST') =>
  fetch(`${process.env.E2E_CONTROL_URL}${p}`, { method }).then(async (r) => {
    expect(r.ok, `${p} -> ${r.status}`).toBeTruthy();
    return r.json() as Promise<Record<string, any>>;
  });

const backend = (p: string, init: RequestInit = {}) =>
  fetch(`${process.env.E2E_BACKEND_URL}${p}`, {
    ...init,
    headers: { 'content-type': 'application/json', Authorization: 'Bearer e2e-user-jwt', ...(init.headers ?? {}) },
  });

const node = (id: string, type: string, data: Record<string, unknown> = {}) => ({ id, type, data, position: { x: 0, y: 0 } });
const edge = (source: string, target: string, sourceHandle?: string) => ({ id: `${source}-${target}`, source, target, ...(sourceHandle ? { sourceHandle } : {}) });

async function stackHasWorkflows(): Promise<boolean> {
  try {
    const r = await backend('/e2e/workflows', { method: 'POST', body: '{}' });
    return r.status !== 404;
  } catch {
    return false;
  }
}

async function seedWorkflow(nodes: unknown[], edges: unknown[], name: string): Promise<string> {
  const r = await backend('/e2e/workflows', { method: 'POST', body: JSON.stringify({ name, spec: { nodes, edges } }) });
  expect(r.ok, await r.clone().text()).toBeTruthy();
  return ((await r.json()) as { id: string }).id;
}

async function startRun(workflowId: string): Promise<string> {
  const r = await backend(`/api/v1/workflows/${workflowId}/run`, { method: 'POST', body: JSON.stringify({ inputs: {} }) });
  expect([200, 202], await r.clone().text()).toContain(r.status);
  return ((await r.json()) as { execution_id: string }).execution_id;
}

async function authenticate(page: Page) {
  await page.context().addCookies([{ name: 'is_authenticated', value: '1', url: `http://localhost:${process.env.E2E_FRONTEND_PORT || 3100}` }]);
}

/** Opens the workflow, its Logs tab and the run's detail. */
async function openRun(page: Page, workflowId: string, executionId: string) {
  await authenticate(page);
  await page.goto(`/workflows/${workflowId}`);
  await page.getByRole('tab', { name: 'Logs' }).click();
  await page.getByTestId(`log-row-${executionId}`).click();
  await expect(page.getByTestId('log-detail-status')).toBeVisible();
}

const status = (page: Page) => page.getByTestId('log-detail-status');
const treeItem = (page: Page, label: string | RegExp) => page.getByRole('treeitem', { name: label });

test.beforeAll(async () => {
  test.skip(!(await stackHasWorkflows()), 'the e2e stack has no workflow fixtures (U22); spec not run');
});

test.beforeEach(async () => {
  await control('/close-gate');
  const s = await control('/state', 'GET');
  if (!s.workerRunning) await control('/start-worker');
});

test('@recovery worker killed mid-run: the tree rejoins after a refresh and the run completes once', async ({ page }) => {
  const tag = `ui-${Date.now()}`;
  const workflowId = await seedWorkflow(
    [node('s', 'start'), node('h', 'http', { method: 'POST', url: `${process.env.E2E_BACKEND_URL}/e2e/http/hold/${tag}`, body: '{}' }), node('o', 'output')],
    [edge('s', 'h'), edge('h', 'o')],
    'ui hold'
  );
  const executionId = await startRun(workflowId);
  await openRun(page, workflowId, executionId);
  await expect(page.getByRole('tree', { name: /workflow run/i })).toBeVisible();
  await expect(treeItem(page, /^h, Running/)).toBeVisible({ timeout: 60_000 });

  await control('/kill-worker');
  await control('/start-worker');
  await page.reload(); // a refresh mid-run rejoins the same run
  await page.getByRole('tab', { name: 'Logs' }).click();
  await page.getByTestId(`log-row-${executionId}`).click();
  await expect(status(page)).not.toHaveAttribute('data-status', /completed|failed|cancelled/);

  await control('/open-gate');
  await expect(status(page)).toHaveAttribute('data-status', 'completed', { timeout: 180_000 });
  await expect(treeItem(page, /^h, Completed/)).toBeVisible();

  // The finished run is read from the stored trace and shows the same nodes.
  await page.reload();
  await page.getByRole('tab', { name: 'Logs' }).click();
  await page.getByTestId(`log-row-${executionId}`).click();
  await expect(status(page)).toHaveAttribute('data-status', 'completed');
  await expect(treeItem(page, /^h, Completed/)).toBeVisible();
});

test('@recovery a human-in-the-loop node waits with its wake time; in-page Approve is refused for this role, the admin decision resumes the run', async ({ page }) => {
  const workflowId = await seedWorkflow(
    [node('s', 'start'), node('approve', 'human_in_loop', { title: 'E2E_APPROVE', slaHours: 1 }), node('o', 'output')],
    [edge('s', 'approve'), edge('approve', 'o')],
    'ui human'
  );
  const executionId = await startRun(workflowId);
  await openRun(page, workflowId, executionId);

  await expect(treeItem(page, /Waiting$/).first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId(/^wake-/)).toBeVisible(); // the SLA wake time
  await expect(page.getByRole('link', { name: /decide in inbox/i })).toHaveAttribute('href', '/inbox');

  await page.getByRole('button', { name: /^Approve / }).click();
  await expect(page.getByRole('alert')).toContainText(/cannot decide|ask an admin/i);
  await expect(page.getByRole('button', { name: /^Approve / })).toBeDisabled();

  await control('/decide-approval?who=admin&decision=approve');
  await expect(status(page)).toHaveAttribute('data-status', 'completed', { timeout: 120_000 });
});

test('@recovery a skipped branch reads skipped in the finished run', async ({ page }) => {
  const workflowId = await seedWorkflow(
    [
      node('s', 'start'),
      node('c', 'ifelse', { conditions: [{ variable: 'sys.query', operator: 'equals', value: '__never__' }] }),
      node('yes', 'output', { label: 'Yes branch' }),
      node('no', 'output', { label: 'No branch' }),
    ],
    [edge('s', 'c'), edge('c', 'yes', 'true'), edge('c', 'no', 'false')],
    'ui branch'
  );
  const executionId = await startRun(workflowId);
  await openRun(page, workflowId, executionId);
  await expect(status(page)).toHaveAttribute('data-status', 'completed', { timeout: 120_000 });
  await expect(page.locator('[role="treeitem"][data-status="skipped"]')).toHaveCount(1);
});
