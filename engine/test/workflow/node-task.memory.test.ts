import { memoryBackend } from "../agent/support.js";
import { defineNodeTaskSuite } from "./node-task.suite.js";

defineNodeTaskSuite(memoryBackend);
