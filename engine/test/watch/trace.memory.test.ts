import { memoryBackend } from "../agent/support.js";
import { defineTraceSuite } from "./trace.suite.js";

defineTraceSuite(memoryBackend);
