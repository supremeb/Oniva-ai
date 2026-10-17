import { usePostgres } from "../support/pg.js";
import { postgresBackend } from "./support.js";
import { defineTaintSuite } from "./taint-suite.js";

defineTaintSuite(postgresBackend(usePostgres()));
