import { defineStructuredCompactionSuite } from "./compaction.structured.suite.js";
import { memoryBackend } from "./support.js";

defineStructuredCompactionSuite(memoryBackend);
