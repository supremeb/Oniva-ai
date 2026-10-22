/**
 * `complete_task` (R16, KTD13): how a subagent with an `outputSchema` hands
 * back a typed result.
 *
 * The tool runs through the engine-phase seam (KTD12). It reads the schema
 * from the calling conversation's own `agent.metadata.outputSchema` (the model
 * never supplies it), validates `result` and, in one commit with the tool
 * result, stores it in the `agent.result` document and ends the child's run
 * (`terminate`). A result that fails validation comes back as a tool error
 * listing the violations; the child can call the tool again.
 */
import { z } from "zod";
import type { JsonObject, JsonValue } from "../core/json.js";
import { defineDoc } from "../session/documents.js";
import { defineTool, type ToolDefinition } from "../tools/registry.js";
import { schemaProblems, validateAgainstSchema } from "./json-schema-subset.js";
import { agentConfigDoc, jsonValue } from "./prompt.js";

export const COMPLETE_TASK_TOOL = "complete_task";

export type AgentResult = { result: JsonValue } | null;

/** The validated result of a child conversation (`null` until `complete_task` succeeds). */
export const agentResultDoc = defineDoc<AgentResult>({
  kind: "agent.result",
  scope: "conversation",
  initial: null,
  schema: z.object({ result: jsonValue }).nullable() as z.ZodType<AgentResult>,
});

/** The output schema recorded in a child's `agent.metadata`, if any and well formed. */
export function metadataOutputSchema(metadata: Record<string, JsonValue>): JsonObject | null {
  const s = metadata.outputSchema;
  if (typeof s !== "object" || s === null || Array.isArray(s)) return null;
  return schemaProblems(s).length === 0 ? s : null;
}

export function createCompleteTaskTool(): ToolDefinition<{ result: JsonValue }> {
  return defineTool<{ result: JsonValue }>({
    name: COMPLETE_TASK_TOOL,
    description:
      "Finish the task by returning the final result. `result` must match the output schema given in your instructions.",
    parameters: z.object({
      result: jsonValue.describe("The final result, matching the output schema."),
    }),
    internal: true,
    enginePhase: {
      phase: "engine",
      run: (_deps, task, rt, writeResult) => {
        const args = task.checkpoint?.arguments ?? {};
        return rt.commit(async (tx) => {
          const agent = (await tx.doc(agentConfigDoc, task.conversationId)).value;
          const schema = metadataOutputSchema(agent.metadata);
          if (schema === null) {
            return writeResult(
              tx,
              task,
              "error",
              {
                content: "complete_task is not available here: this task has no output schema.",
                isError: true,
              },
              0,
            );
          }
          const result = args.result;
          if (result === undefined) {
            return writeResult(
              tx,
              task,
              "invalid_arguments",
              { content: "complete_task needs a `result` argument.", isError: true },
              0,
            );
          }
          const violations = validateAgainstSchema(schema, result);
          if (violations.length > 0) {
            return writeResult(
              tx,
              task,
              "error",
              {
                content: `The result does not match the output schema; fix it and call complete_task again:\n${violations.map((v) => `- ${v}`).join("\n")}`,
                isError: true,
                details: { violations },
              },
              0,
            );
          }
          (await tx.doc(agentResultDoc, task.conversationId)).set({ result });
          return writeResult(tx, task, "ok", { content: "Result recorded.", terminate: true }, 1);
        });
      },
    },
    execute: () => ({
      content: "complete_task runs only inside the agent loop.",
      isError: true,
    }),
  });
}
