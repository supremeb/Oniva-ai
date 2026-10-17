import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, it } from "vitest";
import { uuidv7 } from "../../src/core/ids.js";
import { loadMigrations, migrate } from "../../src/storage/postgres/migrate.js";
import { usePostgres } from "../support/pg.js";

const env = usePostgres();
const dirs: string[] = [];

afterEach(async () => {
  for (const d of dirs.splice(0)) await rm(d, { recursive: true, force: true });
});

async function migrationsDir(files: Record<string, string>): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), "engine-migrations-"));
  dirs.push(dir);
  for (const [name, sql] of Object.entries(files)) await writeFile(join(dir, name), sql);
  return dir;
}

/** A pool on a brand-new, empty schema. */
async function freshSchema() {
  const schema = `mig_${uuidv7().replaceAll("-", "_")}`;
  await env.pool.query(`CREATE SCHEMA ${schema}`);
  const pool = env.newPool({ options: `-c search_path=${schema}` });
  const exists = async (table: string) => {
    const { rows } = await env.pool.query("SELECT to_regclass($1) AS t", [`${schema}.${table}`]);
    return rows[0].t !== null;
  };
  return { schema, pool, exists };
}

describe("loadMigrations", () => {
  it("rejects a badly named file", async () => {
    for (const name of ["init.sql", "1-init.sql", "001_in it.sql", "001_.sql"]) {
      const dir = await migrationsDir({ [name]: "SELECT 1;" });
      await assert.rejects(loadMigrations(dir), /is not named NNN_name\.sql/);
    }
  });

  it("rejects duplicate versions and mixed widths", async () => {
    const dup = await migrationsDir({ "001_a.sql": "SELECT 1;", "001_b.sql": "SELECT 2;" });
    await assert.rejects(loadMigrations(dup), /duplicate migration version 001/);
    const mixed = await migrationsDir({ "001_a.sql": "SELECT 1;", "02_b.sql": "SELECT 2;" });
    await assert.rejects(loadMigrations(mixed), /mixed widths/);
  });

  it("orders by numeric version and checksums each file", async () => {
    const dir = await migrationsDir({
      "010_c.sql": "SELECT 3;",
      "002_b.sql": "SELECT 2;",
      "001_a.sql": "SELECT 1;",
      "notes.txt": "ignored",
    });
    const ms = await loadMigrations(dir);
    assert.deepEqual(
      ms.map((m) => [m.version, m.name]),
      [
        ["001", "a"],
        ["002", "b"],
        ["010", "c"],
      ],
    );
    assert.match(ms[0]?.checksum ?? "", /^[0-9a-f]{64}$/);
    assert.notEqual(ms[0]?.checksum, ms[1]?.checksum);
  });
});

describe("migrate", () => {
  it("rolls back fully when a later file has invalid SQL", async () => {
    const { pool, exists } = await freshSchema();
    const dir = await migrationsDir({
      "001_ok.sql": "CREATE TABLE mig_one (id int);",
      "002_bad.sql": "CREATE TABLE mig_two (id int); SELEC broken;",
    });
    await assert.rejects(migrate(pool, { dir }), /syntax error/);
    assert.equal(await exists("mig_one"), false);
    assert.equal(await exists("mig_two"), false);
    // Not even the bookkeeping table (and so no version row) survives.
    assert.equal(await exists("engine_migrations"), false);
  });

  it("applies the engine migrations exactly once under concurrent migrators", async () => {
    const { schema, pool, exists } = await freshSchema();
    const other = env.newPool({ options: `-c search_path=${schema}` });
    const results = await Promise.all([migrate(pool), migrate(other), migrate(pool)]);
    assert.deepEqual(results.map((r) => r.join(",")).sort(), ["", "", "001,002"]);
    assert.equal(await exists("engine_sessions"), true);
    assert.equal(await exists("engine_usage_outbox"), true);
    const { rows } = await pool.query(
      "SELECT version, checksum FROM engine_migrations ORDER BY version",
    );
    assert.equal(rows.length, 2);
    assert.deepEqual(
      rows.map((r) => r.version),
      ["001", "002"],
    );
    for (const r of rows) assert.match(r.checksum, /^[0-9a-f]{64}$/);
  });

  it("detects a migration modified after being applied", async () => {
    const { pool } = await freshSchema();
    const dir = await migrationsDir({ "001_a.sql": "CREATE TABLE mig_a (id int);" });
    assert.deepEqual(await migrate(pool, { dir }), ["001"]);
    assert.deepEqual(await migrate(pool, { dir }), []);
    await writeFile(join(dir, "001_a.sql"), "CREATE TABLE mig_a (id bigint);");
    await writeFile(join(dir, "002_b.sql"), "CREATE TABLE mig_b (id int);");
    await assert.rejects(migrate(pool, { dir }), /migration 001 was modified after being applied/);
    // Nothing from the failed run was applied.
    const { rows } = await pool.query("SELECT version FROM engine_migrations");
    assert.deepEqual(
      rows.map((r) => r.version),
      ["001"],
    );
  });

  it("adopts the checksum of a migration recorded before checksums existed", async () => {
    const { pool } = await freshSchema();
    const dir = await migrationsDir({ "001_a.sql": "CREATE TABLE mig_a (id int);" });
    await pool.query(`CREATE TABLE engine_migrations (
      version text PRIMARY KEY, name text NOT NULL,
      applied_at timestamptz NOT NULL DEFAULT now())`);
    await pool.query("CREATE TABLE mig_a (id int)");
    await pool.query("INSERT INTO engine_migrations (version, name) VALUES ('001', 'a')");
    assert.deepEqual(await migrate(pool, { dir }), []);
    const [m] = await loadMigrations(dir);
    const { rows } = await pool.query("SELECT checksum FROM engine_migrations");
    assert.equal(rows[0].checksum, m?.checksum);
  });

  it("refuses to run without an existing schema on the search_path", async () => {
    const pool = env.newPool({ options: "-c search_path=no_such_schema_here" });
    await assert.rejects(migrate(pool), /no existing schema on the connection's search_path/);
  });
});
