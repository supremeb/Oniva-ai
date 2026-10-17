import { usePostgres } from "../support/pg.js";
import { defineQuestionsSuite } from "./questions-suite.js";
import { postgresBackend } from "./support.js";

defineQuestionsSuite(postgresBackend(usePostgres()));
