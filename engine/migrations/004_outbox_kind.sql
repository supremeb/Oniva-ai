-- Oniva durable engine: generalise the outbox with a row kind (phase 6 U5).
--
-- The usage outbox (002) also carries `run_finished` rows: a settled run for
-- the backend's run reconciler. Existing rows are usage reports. The `body`
-- column keeps its name and holds each kind's exact POST payload, so a worker
-- still on the previous release keeps working during a rolling deploy. Version
-- 003 is reserved for the approvals work, and the runner tolerates a gap.

ALTER TABLE engine_usage_outbox ADD COLUMN kind text NOT NULL DEFAULT 'usage';
DROP INDEX engine_usage_outbox_pending;
CREATE INDEX engine_usage_outbox_pending ON engine_usage_outbox (kind, claimed_at, created_at);
