import { usePostgres } from "../support/pg.js";
import { defineAgentSuite } from "./suite.js";
import { postgresBackend } from "./support.js";

defineAgentSuite(postgresBackend(usePostgres()));
