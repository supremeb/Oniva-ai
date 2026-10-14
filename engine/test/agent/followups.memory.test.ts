import { defineFollowupSuite } from "./followups.suite.js";
import { memoryBackend } from "./support.js";

defineFollowupSuite(memoryBackend);
