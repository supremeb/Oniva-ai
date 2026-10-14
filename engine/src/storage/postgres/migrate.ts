/**
 * Ordered plain-SQL migrations (KTD2).
 *
 * Files named `NNN_name.sql` are applied in numeric version order, each
 * recorded in `engine_migrations` with the sha256 of its file; a recorded
 * migration whose file has since changed fails the run. The whole run is one transaction holding an advisory
 * lock scoped to the current schema, so concurrent migrators serialize and a
 * failed file leaves nothing applied. Already-applied versions are skipped,
 * which makes `migrate` idempotent.
 *
 * Unqualified names resolve against the connection's `search_path`; point it
 * at the schema the engine should live in.
 */
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Pool } from "pg";

export interface Migration {
  version: string;
  name: string;
  sql: string;
  /** Hex sha256 of the file's bytes. */
  checksum: string;
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

/**
 * Read the `.sql` files of `dir` in numeric version order. Every file must be
 * named `NNN_name.sql`, versions must be unique and all of the same width.
 */
export async function loadMigrations(dir: string = defaultMigrationsDir()): Promise<Migration[]> {
  const files = (await readdir(dir)).filter((f) => f.endsWith(".sql"));
  const parsed: Array<{ file: string; version: string; name: string }> = [];
  for (const file of files) {
    const m = FILE_RE.exec(file);
    if (!m) throw new Error(`migration file ${file} is not named NNN_name.sql`);
    parsed.push({ file, version: m[1] as string, name: m[2] as string });
  }
  const width = parsed[0]?.version.length;
  for (const p of parsed) {
    if (p.version.length !== width) {
      throw new Error(`migration versions have mixed widths (${p.file}); zero-pad them equally`);
    }
  }
  parsed.sort((a, b) => {
    const d = BigInt(a.version) - BigInt(b.version);
    return d < 0n ? -1 : d > 0n ? 1 : 0;
  });
  const out: Migration[] = [];
  for (const [i, p] of parsed.entries()) {
    if (i > 0 && BigInt(parsed[i - 1]?.version as string) === BigInt(p.version)) {
      throw new Error(`duplicate migration version ${p.version}`);
    }
    const bytes = await readFile(join(dir, p.file));
    out.push({
      version: p.version,
      name: p.name,
      sql: bytes.toString("utf8"),
      checksum: createHash("sha256").update(bytes).digest("hex"),
    });
  }
  return out;
}

/**
 * Apply every pending migration. Returns the versions applied by this call.
 * Throws, applying nothing, when an applied migration's file has changed.
 */
export async function migrate(pool: Pool, options: MigrateOptions = {}): Promise<string[]> {
  const migrations = await loadMigrations(options.dir);
  const client = await pool.connect();
  let broken: Error | undefined;
  try {
    await client.query("BEGIN");
    try {
      // Lock per schema. With no existing schema on the search_path,
      // current_schema() is NULL and the lock would silently not be taken.
      const { rows: target } = await client.query<{ schema: string | null }>(
        // pg_advisory_xact_lock is strict: a NULL key is a no-op, caught below.
        `SELECT s AS schema, pg_advisory_xact_lock(hashtext('engine_migrations:' || s))
           FROM current_schema() AS s`,
      );
      if (!target[0]?.schema) {
        throw new Error("cannot migrate: no existing schema on the connection's search_path");
      }
      await client.query(`CREATE TABLE IF NOT EXISTS engine_migrations (
        version     text PRIMARY KEY,
        name        text NOT NULL,
        checksum    text,
        applied_at  timestamptz NOT NULL DEFAULT now()
      )`);
      // Tables created before checksums were recorded.
      await client.query("ALTER TABLE engine_migrations ADD COLUMN IF NOT EXISTS checksum text");
      const { rows } = await client.query<{ version: string; checksum: string | null }>(
        "SELECT version, checksum FROM engine_migrations",
      );
      const applied = new Map(rows.map((r) => [r.version, r.checksum]));
      const done: string[] = [];
      for (const m of migrations) {
        if (applied.has(m.version)) {
          const recorded = applied.get(m.version);
          if (recorded === null) {
            // Applied before checksums existed: adopt the current file.
            await client.query("UPDATE engine_migrations SET checksum = $2 WHERE version = $1", [
              m.version,
              m.checksum,
            ]);
          } else if (recorded !== m.checksum) {
            throw new Error(`migration ${m.version} was modified after being applied`);
          }
          continue;
        }
        await client.query(m.sql);
        await client.query(
          "INSERT INTO engine_migrations (version, name, checksum) VALUES ($1, $2, $3)",
          [m.version, m.name, m.checksum],
        );
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
