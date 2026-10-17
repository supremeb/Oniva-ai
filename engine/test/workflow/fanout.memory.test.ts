import { memoryBackend } from "../agent/support.js";
import { defineFanoutSuite } from "./fanout.suite.js";

defineFanoutSuite(memoryBackend);
