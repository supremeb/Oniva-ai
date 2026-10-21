import { postgresBackend } from "../agent/support.js";
import { usePostgres } from "../support/pg.js";
import { defineCurateTaskSuite } from "./curate-task.suite.js";

defineCurateTaskSuite(postgresBackend(usePostgres()));
