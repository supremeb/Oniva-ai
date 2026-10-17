import { postgresBackend } from "../agent/support.js";
import { usePostgres } from "../support/pg.js";
import { defineHostTaskSuite } from "./suite.js";

defineHostTaskSuite(postgresBackend(usePostgres()));
