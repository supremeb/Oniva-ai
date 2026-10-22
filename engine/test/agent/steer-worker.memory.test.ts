import { defineSteerWorkerSuite } from "./steer-worker.suite.js";
import { memoryBackend } from "./support.js";

defineSteerWorkerSuite(memoryBackend);
