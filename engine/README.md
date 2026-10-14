# @oniva/engine

Oniva's durable agent engine: a TypeScript (ESM, strict, Node >= 22) runtime
that keeps agent sessions alive across worker crashes, long approval waits and
lease takeovers, with Postgres as the system of record.

## Layout

- `src/core/` shared primitives
  - `ids` time-ordered, kind-prefixed UUIDv7 ids (`conv_`, `ent_`, `task_`, `subm_`, `appr_`)
  - `clock` injected `Clock` with real and manual implementations
  - `context` `AbortSignal`-based cancellation with deadlines and reasons
  - `errors` typed engine errors with stable `code`s
  - `json` strict JSON validation
  - `limits` engine limits and their defaults
- `test/` unit and integration tests; `test/scenario/` the bake-off scenarios

## Commands

```sh
npm ci
npm run typecheck       # tsc --noEmit
npm run lint            # biome check
npm test                # unit + integration (excludes test/scenario)
npm run test:scenario   # bake-off scenario suite
npm run build           # emit dist/
```

Integration tests use `ENGINE_TEST_PG_URL` when set (CI provides Postgres 16).

## Limits

| Limit | Default |
| --- | --- |
| `maxToolCallsPerRound` | 32 |
| `maxSubagentDepth` | 3 |
| `maxRequestBodyBytes` | 1 MiB |
| `maxEntryBytes` | 256 KiB |
| `maxDocumentBytes` | 1 MiB |

Override any of them with `resolveLimits({...})`.
