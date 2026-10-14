import { afterEach, beforeEach, describe, it } from "vitest";
import { defineStorageConformanceSuite } from "../../src/storage/conformance.js";
import { MemoryStorageProvider } from "../../src/storage/memory.js";

describe("memory backend", () => {
  defineStorageConformanceSuite({ describe, it, beforeEach, afterEach }, async () => ({
    provider: new MemoryStorageProvider(),
    fence: async () => null,
  }));
});
