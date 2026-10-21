import { usePostgres } from "../support/pg.js";
import { defineOffloadSuite } from "./offload.suite.js";
import { postgresBackend } from "./support.js";

defineOffloadSuite(postgresBackend(usePostgres()));
