import { usePostgres } from "../support/pg.js";
import { defineCompleteTaskSuite } from "./complete-task.suite.js";
import { postgresBackend } from "./support.js";

defineCompleteTaskSuite(postgresBackend(usePostgres()));
