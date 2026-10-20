import { memoryBackend } from "../agent/support.js";
import { defineRunTaskSuite } from "./run-task.suite.js";

defineRunTaskSuite(memoryBackend);
