import { usePostgres } from "../support/pg.js";
import { defineFollowupSuite } from "./followups.suite.js";
import { postgresBackend } from "./support.js";

defineFollowupSuite(postgresBackend(usePostgres()));
