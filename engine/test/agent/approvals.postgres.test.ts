import { usePostgres } from "../support/pg.js";
import { defineApprovalSuite } from "./approvals-suite.js";
import { postgresBackend } from "./support.js";

defineApprovalSuite(postgresBackend(usePostgres()));
