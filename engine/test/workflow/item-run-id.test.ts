import { describe, expect, it } from "vitest";
import { itemRunId } from "../../src/workflow/fanout-lane-task.js";

// The same vectors are asserted by the backend (`tests/internal/test_workflow_contract.py`): the
// engine derives the item run ids the backend's fan-out `prepare` creates its rows under.
const RUN = "11111111-2222-4333-8444-555555555555";

describe("itemRunId", () => {
  it("matches the backend's uuid5 ids; attempt 0 is unchanged and a run retry gets its own rows", () => {
    expect(itemRunId(RUN, "it", 3)).toBe("37932702-6532-5d4d-9214-d6018247c159");
    expect(itemRunId(RUN, "it", 3, 0)).toBe("37932702-6532-5d4d-9214-d6018247c159");
    expect(itemRunId(RUN, "it", 3, 1)).toBe("3eee08c0-175b-5dab-91f8-ceebd1a45d71");
    expect(itemRunId(RUN, "it", 0, 2)).toBe("29f7125e-f475-5c69-a157-57eba33e46b6");
  });

  it("a non-UUID run id keeps its readable form (its node keys carry the run attempt)", () => {
    expect(itemRunId("run-1", "n", 2)).toBe("run-1:n:i2");
    expect(itemRunId("run-1", "n", 2, 1)).toBe("run-1:n:i2");
  });
});
