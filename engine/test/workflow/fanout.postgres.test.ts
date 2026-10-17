import { postgresBackend } from "../agent/support.js";
import { usePostgres } from "../support/pg.js";
import { defineFanoutSuite } from "./fanout.suite.js";

defineFanoutSuite(postgresBackend(usePostgres()));
