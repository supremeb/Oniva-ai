import { defineSubagentContextSuite } from "./subagent-context.suite.js";
import { memoryBackend } from "./support.js";

defineSubagentContextSuite(memoryBackend);
