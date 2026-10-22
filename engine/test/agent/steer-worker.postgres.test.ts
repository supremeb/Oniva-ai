import { usePostgres } from "../support/pg.js";
import { defineSteerWorkerSuite } from "./steer-worker.suite.js";
import { postgresBackend } from "./support.js";

defineSteerWorkerSuite(postgresBackend(usePostgres()));
