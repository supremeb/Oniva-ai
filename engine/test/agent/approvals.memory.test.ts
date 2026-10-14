import { defineApprovalSuite } from "./approvals-suite.js";
import { memoryBackend } from "./support.js";

defineApprovalSuite(memoryBackend);
