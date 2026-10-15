import { usePostgres } from "../support/pg.js";
import { defineTaskRuntimeSuite } from "./suite.js";
import { postgresBackend } from "./support.js";

defineTaskRuntimeSuite(postgresBackend(usePostgres()));
