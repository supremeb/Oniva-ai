import { usePostgres } from "../support/pg.js";
import { defineSubagentSuite } from "./subagents.suite.js";
import { postgresBackend } from "./support.js";

defineSubagentSuite(postgresBackend(usePostgres()));
