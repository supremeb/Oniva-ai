/**
 * Postgres for integration tests.
 *
 * With `ENGINE_TEST_PG_URL` set, tests use that database. Otherwise each test
 * file boots a throwaway cluster (`initdb` + `pg_ctl`) on a free port in a temp
 * directory and stops it afterwards. Either way each file works in its own
 * fresh schema, migrated by the engine's migration runner, and drops it at the
 * end.
 *
 * Binaries are found via `ENGINE_TEST_PG_BIN`, then `PATH`, then common
 * install locations.
 */
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { appendFile, mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";
import { promisify } from "node:util";
import pg from "pg";
import { afterAll, beforeAll } from "vitest";
import { migrate } from "../../src/storage/postgres/migrate.js";

const run = promisify(execFile);

const BIN_CANDIDATES = [
  "/opt/homebrew/bin",
  "/usr/local/bin",
  "/opt/homebrew/opt/postgresql@14/bin",
  "/opt/homebrew/opt/postgresql@16/bin",
  "/usr/lib/postgresql/16/bin",
  "/usr/lib/postgresql/14/bin",
];

function findBin(name: string): string {
  const dirs = [
    ...(process.env.ENGINE_TEST_PG_BIN ? [process.env.ENGINE_TEST_PG_BIN] : []),
    ...(process.env.PATH ?? "").split(delimiter).filter(Boolean),
    ...BIN_CANDIDATES,
  ];
  for (const dir of dirs) {
    const p = join(dir, name);
    if (existsSync(p)) return p;
  }
  throw new Error(
    `${name} not found; set ENGINE_TEST_PG_URL or ENGINE_TEST_PG_BIN to run Postgres tests`,
  );
}

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const addr = srv.address();
      const port = typeof addr === "object" && addr ? addr.port : 0;
      srv.close(() => resolve(port));
    });
  });
}

export interface PgServer {
  url: string;
  stop(): Promise<void>;
}

/** Boot a throwaway local cluster. */
export async function startThrowawayCluster(): Promise<PgServer> {
  const initdb = findBin("initdb");
  const pgCtl = findBin("pg_ctl");
  const dir = await mkdtemp(join(tmpdir(), "oniva-pg-"));
  const data = join(dir, "data");
  await run(initdb, ["-D", data, "-U", "postgres", "-A", "trust", "-E", "UTF8", "--no-sync"]);
  const port = await freePort();
  await appendFile(
    join(data, "postgresql.conf"),
    [
      "",
      `port = ${port}`,
      "listen_addresses = '127.0.0.1'",
      "unix_socket_directories = ''",
      "fsync = off",
      "synchronous_commit = off",
      "full_page_writes = off",
      "max_connections = 100",
      "",
    ].join("\n"),
  );
  await run(pgCtl, ["-D", data, "-l", join(dir, "log"), "-w", "-t", "60", "start"]);
  return {
    url: `postgres://postgres@127.0.0.1:${port}/postgres`,
    async stop() {
      try {
        await run(pgCtl, ["-D", data, "-m", "immediate", "-w", "stop"]);
      } finally {
        await rm(dir, { recursive: true, force: true });
      }
    },
  };
}

export interface PgTestEnv {
  /** Connection URL of the server. */
  readonly url: string;
  /** The file's schema; pools from {@link PgTestEnv.newPool} use it. */
  readonly schema: string;
  /** The file's shared pool. */
  readonly pool: pg.Pool;
  /** A new pool on the file's schema; closed automatically after the file. */
  newPool(config?: pg.PoolConfig): pg.Pool;
}

/**
 * Register `beforeAll`/`afterAll` hooks that provide a migrated schema for the
 * current test file. Read the returned object's fields inside tests only.
 */
export function usePostgres(): PgTestEnv {
  let server: PgServer | null = null;
  let url = "";
  const schema = `engine_test_${process.pid}_${Math.random().toString(36).slice(2, 10)}`;
  const pools: pg.Pool[] = [];
  let shared: pg.Pool | null = null;

  const newPool = (config: pg.PoolConfig = {}): pg.Pool => {
    const p = new pg.Pool({
      connectionString: url,
      options: `-c search_path=${schema}`,
      max: 10,
      ...config,
    });
    // Idle clients killed by tests must not crash the process.
    p.on("error", () => {});
    pools.push(p);
    return p;
  };

  beforeAll(async () => {
    const external = process.env.ENGINE_TEST_PG_URL;
    if (external) {
      url = external;
    } else {
      server = await startThrowawayCluster();
      url = server.url;
    }
    const admin = new pg.Client({ connectionString: url });
    await admin.connect();
    try {
      await admin.query(`CREATE SCHEMA ${schema}`);
    } finally {
      await admin.end();
    }
    shared = newPool();
    await migrate(shared);
  }, 120_000);

  afterAll(async () => {
    for (const p of pools.splice(0)) {
      await p.end().catch(() => {});
    }
    try {
      if (url) {
        const admin = new pg.Client({ connectionString: url });
        await admin.connect();
        try {
          await admin.query(`DROP SCHEMA IF EXISTS ${schema} CASCADE`);
        } finally {
          await admin.end();
        }
      }
    } finally {
      await server?.stop();
    }
  }, 60_000);

  return {
    get url() {
      return url;
    },
    schema,
    get pool() {
      if (!shared) throw new Error("usePostgres: pool used before beforeAll ran");
      return shared;
    },
    newPool,
  };
}
