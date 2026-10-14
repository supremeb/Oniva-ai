import { defineSubagentSuite } from "./subagents.suite.js";
import { memoryBackend } from "./support.js";

defineSubagentSuite(memoryBackend);
