import { usePostgres } from "../support/pg.js";
import { defineSubagentContextSuite } from "./subagent-context.suite.js";
import { postgresBackend } from "./support.js";

defineSubagentContextSuite(postgresBackend(usePostgres()));
