#!/usr/bin/env node
// Copies the recorded legacy chat SSE contract fixtures from the backend into
// lib/sse/__fixtures__/. The backend copies are the source of truth
// (backend/tests/contracts/fixtures); a jest drift check fails when they diverge.
//   node scripts/sync-sse-fixtures.mjs
import { copyFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = join(root, '..', 'backend', 'tests', 'contracts', 'fixtures');
const dst = join(root, 'lib', 'sse', '__fixtures__');
mkdirSync(dst, { recursive: true });
for (const name of ['chat_text', 'chat_tool_call', 'chat_error']) {
  copyFileSync(join(src, `${name}.json`), join(dst, `${name}.json`));
  console.log(`synced ${name}.json`);
}
