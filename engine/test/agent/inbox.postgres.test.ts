import { usePostgres } from "../support/pg.js";
import { defineInboxSuite } from "./inbox.suite.js";
import { postgresBackend } from "./support.js";

defineInboxSuite(postgresBackend(usePostgres()));
