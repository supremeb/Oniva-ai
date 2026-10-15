import { defineCompactionSuite } from "./compaction.suite.js";
import { memoryBackend } from "./support.js";

defineCompactionSuite(memoryBackend);
