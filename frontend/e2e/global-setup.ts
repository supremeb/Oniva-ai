import { type ChildProcess, spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const FRONTEND_PORT = Number(process.env.E2E_FRONTEND_PORT || 3100);
const REPO = path.resolve(__dirname, '../..');
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function waitFor<T>(what: string, fn: () => Promise<T | null | false> | T | null | false, timeoutMs: number): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  let last: unknown;
  while (Date.now() < deadline) {
    try {
      const got = await fn();
      if (got) return got;
    } catch (e) {
      last = e;
    }
    await sleep(300);
  }
  throw new Error(`timed out waiting for ${what}${last ? ` (last error: ${String(last)})` : ''}`);
}

function stop(p: ChildProcess | undefined, signal: NodeJS.Signals) {
  if (!p?.pid) return;
  try {
    process.kill(-p.pid, signal);
  } catch {
    /* already gone */
  }
}

/** Boots the stack and Next; exports E2E_CONTROL_URL / E2E_PROJECT_ID for the specs. Returns the teardown. */
export default async function globalSetup() {
  if (process.env.ONIVA_E2E_BROWSER !== '1') return;

  const dir = mkdtempSync(path.join(tmpdir(), 'oniva-pw-'));
  const infoPath = path.join(dir, 'stack.json');
  const origin = `http://localhost:${FRONTEND_PORT}`;
  const children: ChildProcess[] = [];
  const teardown = async () => {
    // The stack runner cleans up Postgres, the backend and the worker on SIGTERM; SIGKILL is the fallback.
    for (const c of children) stop(c, 'SIGTERM');
    const deadline = Date.now() + 40_000;
    while (Date.now() < deadline && children.some((c) => c.exitCode === null && c.signalCode === null)) await sleep(250);
    for (const c of children) stop(c, 'SIGKILL');
    rmSync(dir, { recursive: true, force: true });
  };

  try {
    // The venv's interpreter directly: a `uv run` wrapper does not pass SIGTERM on, which the stack's cleanup needs.
    const venvPython = path.join(REPO, 'backend/.venv/bin/python');
    const [cmd, args] = existsSync(venvPython)
      ? [venvPython, ['-m', 'tests.e2e.stack']]
      : ['uv', ['run', 'python', '-m', 'tests.e2e.stack']];
    const stack = spawn(cmd, args, {
      cwd: path.join(REPO, 'backend'),
      env: { ...process.env, E2E_STACK_INFO: infoPath, E2E_CORS_ORIGIN: origin },
      detached: true,
      stdio: ['ignore', 'inherit', 'inherit'],
    });
    children.push(stack);
    const info = await waitFor('the engine+backend stack', () => {
      if (stack.exitCode !== null) throw new Error(`stack exited with ${stack.exitCode}`);
      return existsSync(infoPath) ? (JSON.parse(readFileSync(infoPath, 'utf8')) as { backendUrl: string; controlUrl: string; projectId: string }) : null;
    }, 180_000);

    // A production build by default: React strict mode's double effect in `next dev` stops the watcher from
    // starting on a page opened with ?c= (a rejoin). E2E_NEXT_MODE=dev runs the dev server instead.
    const nextBin = path.join(__dirname, '../node_modules/.bin/next');
    const nextEnv = { ...process.env, NEXT_PUBLIC_API_URL: info.backendUrl, NEXT_TELEMETRY_DISABLED: '1' };
    const dev = process.env.E2E_NEXT_MODE === 'dev';
    if (!dev) {
      await new Promise<void>((resolve, reject) => {
        // --no-lint: the repo's lint config has a pre-existing rule error that fails `next build`; lint runs in CI.
        const build = spawn(nextBin, ['build', '--no-lint'], { cwd: path.join(__dirname, '..'), env: nextEnv, stdio: ['ignore', 'inherit', 'inherit'] });
        build.on('exit', (code) => (code === 0 ? resolve() : reject(new Error(`next build exited with ${code}`))));
      });
    }
    const next = spawn(nextBin, [dev ? 'dev' : 'start', '-p', String(FRONTEND_PORT)], {
      cwd: path.join(__dirname, '..'),
      env: nextEnv,
      detached: true,
      stdio: ['ignore', 'inherit', 'inherit'],
    });
    children.push(next);
    await waitFor('Next.js', async () => {
      if (next.exitCode !== null) throw new Error(`next exited with ${next.exitCode}`);
      return (await fetch(`${origin}/login`)).status < 500;
    }, 180_000);

    process.env.E2E_CONTROL_URL = info.controlUrl;
    process.env.E2E_BACKEND_URL = info.backendUrl;
    process.env.E2E_PROJECT_ID = info.projectId;
    return teardown;
  } catch (e) {
    await teardown();
    throw e;
  }
}
