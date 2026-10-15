import { defineInboxSuite } from "./inbox.suite.js";
import { memoryBackend } from "./support.js";

defineInboxSuite(memoryBackend);
