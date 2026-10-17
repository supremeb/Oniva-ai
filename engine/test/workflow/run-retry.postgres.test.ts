import { postgresBackend } from "../agent/support.js";
import { usePostgres } from "../support/pg.js";
import { defineRunRetrySuite } from "./run-retry.suite.js";

defineRunRetrySuite(postgresBackend(usePostgres()));
