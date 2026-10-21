import { memoryBackend } from "../agent/support.js";
import { defineCurateTaskSuite } from "./curate-task.suite.js";

defineCurateTaskSuite(memoryBackend);
