# Alembic vs models: drift audit (U8)

Date: 2026-10-14. Postgres 14 (local `initdb` cluster), pgvector **not available** for PG14/15
on this machine (Homebrew `pgvector` ships `vector.dylib` only for postgresql@17/18). The audit was
therefore run with a shim (below); the pgvector-specific parts are static only.

## Verdict: STOP condition hit

`alembic upgrade head` **cannot run on an empty database**. The chain was evidently never run from
scratch; it only works against databases built by `Base.metadata.create_all` (and/or hand-patched).
Making it run means editing historical migrations (001, 009, 011, 028, 031, ...), which is not a
"catch-up migration" and is unsafe to do blind. Surfaced, not fixed. Only the safe, additive piece
(`sandbox_executions`, migration 084) was added.

## Blockers found, in the order `upgrade head` hits them (fresh PG14)

| # | Revision | Failure |
|---|---|---|
| 1 | 001_initial_schema | `op.create_index(..., postgresql_using='DESC')` emits `USING desc (created_at)`: syntax error. First migration fails. |
| 2 | 001_initial_schema | Same index also created via `index=True` on the column: `relation ix_audit_logs_created_at already exists`. |
| 3 | 009_users_tenant_id | `DROP CONSTRAINT users_email_key` - constraint does not exist. |
| 4 | 011_rls_policies | `GRANT CONNECT ON DATABASE oniva` hard-codes the DB name; any other name fails. (Tier uses DB `oniva`.) |
| 5 | 028_custom_agents_tenant | `ADD COLUMN tenant_id` already added earlier (005/010). |
| 6 | 031_add_tenant_id_to_workflow_executions | Revision id is 40 chars; `alembic_version.version_num` is `varchar(32)`: `StringDataRightTruncation`. Also `fk_workflow_executions_tenant_id` already exists (added in 010). Fixture pre-creates `alembic_version` as varchar(255). |
| 7 | 034 / 055 | `CREATE TYPE schedule_type/schedule_status/batch_status/batch_item_status`: types already exist (created implicitly by earlier `create_table`). |
| 8 | 039_add_kb_id_index | Index on `documents.doc_metadata`, a column that does not exist. |
| 9 | 059 | `CREATE POLICY tenant_isolation_documents` already exists (043). |
| 10 | 052b / 072 | `CREATE INDEX CONCURRENTLY` inside Alembic's transaction (052b issues manual COMMIT, which then breaks the version bookkeeping: `expected to match one row ... 0 found` at 071 -> 072). |

Rows 2, 3, 5, 6 (FK), 7, 9 are duplicate-object errors: later migrations re-create things earlier
migrations (or create_all) already made. Audit method: SQL rewrite shim + per-statement savepoints
(scratch script, not committed) let the chain run to 071 and collected these; everything after 071
is unaudited because of #10.

## Model vs schema (partial, run stopped at 071)

`compare_metadata` (type/default comparison off) over the partially migrated schema reported ~390
diffs. Dominated by noise (104 `remove_index` / 85 `add_index` name/definition mismatches, ~60
`modify_comment`, ~25 `modify_nullable` on `created_at/updated_at`). Not trustworthy past the
abort, but real signals:

- Models with no migration: `sandbox_executions` (**fixed**: 084).
- Migration tables with no model: `workflow_approvals`, `webhook_endpoints`,
  `subworkflow_executions` (plus `_backup_orphaned_checkpoints_062`).
- Nullability disagreements: `users.created_at/updated_at/is_active/is_superuser` (DB nullable,
  model not), `api_keys.user_id`, `connectors.user_id`, `scheduled_workflows.workflow_id`,
  `episodic_memories.user_id`.
- Tables present only after 072+ were not reached, so "add_table" results for the later tables
  (`knowledge_*`, `projects`, `webhook_*`, ...) are artifacts of the abort, not drift.

## pgvector

- `CREATE EXTENSION vector` is already issued by 001 and 003 (no new migration needed).
- Vector columns/HNSW indexes (032, 064, 068, 003, 7262eef56aaf) were not executed locally.

## Recommended follow-up (own unit)

1. Widen `alembic_version.version_num` in `env.py` (or a pre-001 step) to varchar(255).
2. Make 001-059 idempotent or squash to a baseline revision generated from `Base.metadata` for
   fresh installs, keeping the existing chain for already-deployed DBs (stamp at baseline).
3. Replace `CONCURRENTLY` + manual COMMIT with `autocommit_block()`.
4. Drop the DB-name literal from 011.
5. Once green on CI (pgvector/pg16), remove the strict `xfail` markers in `test_migrations.py`.
