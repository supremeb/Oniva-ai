import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    projects: [
      {
        test: {
          name: "unit",
          include: ["test/**/*.test.ts"],
          exclude: ["test/scenario/**", "**/node_modules/**"],
        },
      },
      {
        test: {
          name: "scenario",
          include: ["test/scenario/**/*.test.ts"],
          testTimeout: 120_000,
          hookTimeout: 120_000,
          // Worker processes and the load measurement need the machine to themselves.
          fileParallelism: false,
        },
      },
    ],
  },
});
