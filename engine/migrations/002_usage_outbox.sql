-- Oniva durable engine: durable usage outbox (U6 / KTD6).
--
-- A usage report is parked here before it is POSTed to the backend ledger and
-- deleted only after a 2xx, so a report survives a backend outage and a worker
-- restart. The outbox is worker-scoped, not session-scoped: its rows are
-- written with plain unfenced single-row statements, outside the session
-- commit line, so they never interact with the lease.
--
-- `created_at` and `claimed_at` are epoch milliseconds (not timestamptz) so the
-- memory backend and this one round-trip identically. A claim is stamped
-- `claimed_at`; the flusher may take a row that is unclaimed or whose claim is
-- older than its claim timeout, oldest first.

CREATE TABLE engine_usage_outbox (
  request_id  text PRIMARY KEY,
  body        json NOT NULL,
  created_at  bigint NOT NULL,
  claimed_at  bigint
);
CREATE INDEX engine_usage_outbox_pending ON engine_usage_outbox (claimed_at, created_at);
