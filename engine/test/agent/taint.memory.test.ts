import { memoryBackend } from "./support.js";
import { defineTaintSuite } from "./taint-suite.js";

defineTaintSuite(memoryBackend);
