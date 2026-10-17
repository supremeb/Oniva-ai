import { postgresBackend } from "../agent/support.js";
import { usePostgres } from "../support/pg.js";
import { defineRunTaskSuite } from "./run-task.suite.js";

defineRunTaskSuite(postgresBackend(usePostgres()));
