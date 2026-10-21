import { usePostgres } from "../support/pg.js";
import { defineStructuredCompactionSuite } from "./compaction.structured.suite.js";
import { postgresBackend } from "./support.js";

defineStructuredCompactionSuite(postgresBackend(usePostgres()));
