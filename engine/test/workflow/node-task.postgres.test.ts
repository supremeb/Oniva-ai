import { postgresBackend } from "../agent/support.js";
import { usePostgres } from "../support/pg.js";
import { defineNodeTaskSuite } from "./node-task.suite.js";

defineNodeTaskSuite(postgresBackend(usePostgres()));
