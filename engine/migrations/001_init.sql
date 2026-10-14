-- Oniva durable engine: initial schema (Postgres 14+).
--
-- Every table is keyed by session_id. Records are stored whole as `json`
-- (which keeps the exact text, unlike jsonb), with the columns needed for
-- lookups and scans extracted alongside. `pos` is a per-session creation
-- position used for scan order and cursors; `seq` is the commit that last
-- wrote the row.

-- Per-session counters: the gap-free commit seq and the next creation position.
CREATE TABLE engine_sessions (
  session_id  text PRIMARY KEY,
  last_seq    bigint NOT NULL DEFAULT 0 CHECK (last_seq >= 0),
  next_pos    bigint NOT NULL DEFAULT 0 CHECK (next_pos >= 0),
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Session ownership. Rows are never deleted, so a token is never reused:
-- release only expires the lease, and every takeover bumps the token.
CREATE TABLE engine_session_leases (
  session_id   text PRIMARY KEY,
  owner_id     text NOT NULL,
  token        bigint NOT NULL CHECK (token > 0),
  expires_at   timestamptz NOT NULL,
  acquired_at  timestamptz NOT NULL,
  renewed_at   timestamptz NOT NULL
);

-- One row per accepted commit: seq is 1, 2, 3, ... per session with no gaps.
CREATE TABLE engine_commits (
  session_id    text NOT NULL,
  seq           bigint NOT NULL CHECK (seq > 0),
  fence_token   bigint,
  record_count  integer NOT NULL,
  committed_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (session_id, seq)
);

-- Conversation ids are unique across sessions; the primary key doubles as the
-- conversation_id -> session_id lookup.
CREATE TABLE engine_conversations (
  id          text PRIMARY KEY,
  session_id  text NOT NULL,
  pos         bigint NOT NULL,
  seq         bigint NOT NULL,
  record      json NOT NULL
);
CREATE INDEX engine_conversations_scan ON engine_conversations (session_id, pos);

-- Task ids are unique across sessions; the primary key doubles as the
-- (tool) task_id -> session_id lookup.
CREATE TABLE engine_tasks (
  id               text PRIMARY KEY,
  session_id       text NOT NULL,
  conversation_id  text NOT NULL,
  status           text NOT NULL,
  pos              bigint NOT NULL,
  seq              bigint NOT NULL,
  record           json NOT NULL
);
CREATE INDEX engine_tasks_scan ON engine_tasks (session_id, pos);
CREATE INDEX engine_tasks_conversation ON engine_tasks (session_id, conversation_id, pos);

CREATE TABLE engine_entries (
  session_id       text NOT NULL,
  id               text NOT NULL,
  conversation_id  text NOT NULL,
  pos              bigint NOT NULL,
  seq              bigint NOT NULL,
  record           json NOT NULL,
  PRIMARY KEY (session_id, id)
);
CREATE INDEX engine_entries_conversation ON engine_entries (session_id, conversation_id, pos);

CREATE TABLE engine_submissions (
  session_id       text NOT NULL,
  id               text NOT NULL,
  conversation_id  text NOT NULL,
  request_id       text NOT NULL,
  status           text NOT NULL,
  pos              bigint NOT NULL,
  seq              bigint NOT NULL,
  record           json NOT NULL,
  PRIMARY KEY (session_id, id),
  UNIQUE (session_id, conversation_id, request_id)
);
CREATE INDEX engine_submissions_conversation
  ON engine_submissions (session_id, conversation_id, pos);

CREATE TABLE engine_documents (
  session_id  text NOT NULL,
  kind        text NOT NULL,
  scope_id    text NOT NULL,
  pos         bigint NOT NULL,
  seq         bigint NOT NULL,
  record      json NOT NULL,
  PRIMARY KEY (session_id, kind, scope_id)
);
CREATE INDEX engine_documents_scan ON engine_documents (session_id, pos);
CREATE INDEX engine_documents_kind ON engine_documents (session_id, kind, pos);
