/**
 * Ordered plain-SQL migrations (KTD2).
 *
 * Files named `NNN_name.sql` are applied in version order, each recorded in
 * `engine_migrations`. The whole run is one transaction holding an advisory
 * lock scoped to the current schema, so concurrent migrators serialize and a
 * failed file leaves nothing applied. Already-applied versions are skipped,
 * which makes `migrate` idempotent.
 *
 * Unqualified names resolve against the connection's `search_path`; point it
 * at the schema the engine should live in.
 */
import { existsSync } from "node:fs";
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Pool } from "pg";

export interface Migration {
  version: string;
  name: string;
  sql: string;
}

export interface MigrateOptions {
  /** Directory of `NNN_name.sql` files. Defaults to the package's `migrations/`. */
  dir?: string;
}

const FILE_RE = /^(\d+)_([A-Za-z0-9_-]+)\.sql$/;

/** The package's bundled migrations directory (works from `src/` and `dist/src/`). */
export function defaultMigrationsDir(): string {
  const here = fileURLToPath(new URL(".", import.meta.url));
  for (const up of ["../../../migrations", "../../../../migrations"]) {
    const dir = join(here, up);
    if (existsSync(dir)) return dir;
  }
  throw new Error("engine migrations directory not found; pass `dir`");
}

export async function loadMigrations(dir: string = defaultMigrationsDir()): Promise<Migration[]> {
  const files = (await readdir(dir)).filter((f) => f.endsWith(".sql")).sort();
  const out: Migration[] = [];
  const seen = new Set<string>();
  for (const file of files) {
    const m = FILE_RE.exec(file);
    if (!m) throw new Error(`migration file ${file} is not named NNN_name.sql`);
    const version = m[1] as string;
    if (seen.has(version)) throw new Error(`duplicate migration version ${version}`);
    seen.add(version);
    out.push({ version, name: m[2] as string, sql: await readFile(join(dir, file), "utf8") });
  }
  return out;
}

/** Apply every pending migration. Returns the versions applied by this call. */
export async function migrate(pool: Pool, options: MigrateOptions = {}): Promise<string[]> {
  const migrations = await loadMigrations(options.dir);
  const client = await pool.connect();
  let broken: Error | undefined;
  try {
    await client.query("BEGIN");
    try {
      await client.query(
        "SELECT pg_advisory_xact_lock(hashtext('engine_migrations:' || current_schema()))",
      );
      await client.query(`CREATE TABLE IF NOT EXISTS engine_migrations (
        version     text PRIMARY KEY,
        name        text NOT NULL,
        applied_at  timestamptz NOT NULL DEFAULT now()
      )`);
      const { rows } = await client.query<{ version: string }>(
        "SELECT version FROM engine_migrations",
      );
      const applied = new Set(rows.map((r) => r.version));
      const done: string[] = [];
      for (const m of migrations) {
        if (applied.has(m.version)) continue;
        await client.query(m.sql);
        await client.query("INSERT INTO engine_migrations (version, name) VALUES ($1, $2)", [
          m.version,
          m.name,
        ]);
        done.push(m.version);
      }
      await client.query("COMMIT");
      return done;
    } catch (err) {
      await client.query("ROLLBACK").catch((e: unknown) => {
        broken = e instanceof Error ? e : new Error(String(e));
      });
      throw err;
    }
  } finally {
    client.release(broken);
  }
}
