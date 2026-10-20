import { defineQuestionsSuite } from "./questions-suite.js";
import { memoryBackend } from "./support.js";

defineQuestionsSuite(memoryBackend);
