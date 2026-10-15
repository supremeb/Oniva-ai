import { usePostgres } from "../support/pg.js";
import { defineCompactionSuite } from "./compaction.suite.js";
import { postgresBackend } from "./support.js";

defineCompactionSuite(postgresBackend(usePostgres()));
