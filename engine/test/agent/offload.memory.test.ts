import { defineOffloadSuite } from "./offload.suite.js";
import { memoryBackend } from "./support.js";

defineOffloadSuite(memoryBackend);
