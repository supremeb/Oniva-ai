import { usePostgres } from "../support/pg.js";
import { defineContextClearingSuite } from "./context-clearing.suite.js";
import { postgresBackend } from "./support.js";

defineContextClearingSuite(postgresBackend(usePostgres()));
