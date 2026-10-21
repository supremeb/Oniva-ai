import { postgresBackend } from "../agent/support.js";
import { usePostgres } from "../support/pg.js";
import { defineTraceSuite } from "./trace.suite.js";

defineTraceSuite(postgresBackend(usePostgres()));
