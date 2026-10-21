import { defineCompleteTaskSuite } from "./complete-task.suite.js";
import { memoryBackend } from "./support.js";

defineCompleteTaskSuite(memoryBackend);
