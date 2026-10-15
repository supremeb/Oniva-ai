import { defineTaskRuntimeSuite } from "./suite.js";
import { memoryBackend } from "./support.js";

defineTaskRuntimeSuite(memoryBackend);
