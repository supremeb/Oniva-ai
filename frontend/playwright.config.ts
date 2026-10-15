import { defineConfig } from '@playwright/test';

/**
 * Browser e2e (U12). Runs only with ONIVA_E2E_BROWSER=1: global setup boots the engine+backend stack
 * (backend/tests/e2e/stack.py, needs Postgres binaries, uv and the engine's node_modules) and a Next.js
 * server pointed at it. See e2e/global-setup.ts.
 *
 *   ONIVA_E2E_BROWSER=1 npx playwright test chat-engine-recovery
 */
export const FRONTEND_PORT = Number(process.env.E2E_FRONTEND_PORT || 3100);

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  expect: { timeout: 30_000 },
  reporter: [['list']],
  use: {
    baseURL: `http://localhost:${FRONTEND_PORT}`,
    trace: 'retain-on-failure',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
