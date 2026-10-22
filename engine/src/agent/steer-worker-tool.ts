/**
 * `steer_worker` (R18, KTD14): the parent steers a running subagent by label.
 *
 * The tool runs through the engine-phase seam. It looks only at the calling
 * run's own subagent tool tasks (same conversation, same run), keeps those
 * that are not terminal, carry the given `label` in their checkpoint and whose
 * child is working right now, and submits the message to each child with
 * `whenBusy: "steer"`. The child sees it at its next tool-round boundary,
 * wrapped so it can tell the orchestrator from a user.
 *
 * Idempotent: the submission to a child has `requestId =
 * steer:<subagent tool task id>:<this call id>`, so a rerun of the call (after
 * a crash, or a duplicate) finds the stored submission and sends nothing new.
 * The submissions and the tool result are written in one commit.
 *
 * More than one live task with the label: all of them are steered. A child
 * that is idle (it already answered, the parent has not woken yet) is not live
 * and is not steered, so a steer never starts a fresh run on a finished child.
 */
import { z } from "zod";
import type { JsonValue } from "../core/json.js";
import { waiting } from "../tasks/define.js";
import { defineTool, type ToolDefinition } from "../tools/registry.js";
import { isBusy } from "./conversation.js";
import { Inbox } from "./inbox.js";
import { isSubagentTool } from "./subagents.js";
import { type SubmissionInputRecord, toSubmissionInput } from "./submission.js";
import type { ToolTaskCheckpoint, ToolTaskInput } from "./tool-task.js";

export const STEER_WORKER_TOOL = "steer_worker";

/** Prefix of every steer, so the child can tell the orchestrator from a user. */
export const STEER_PREFIX = "Message from the orchestrator: ";

/** Longest message `steer_worker` accepts. */
export const STEER_MAX_CHARS = 4000;

/**
 * A worker started in the same round as the steer may not have created its
 * child yet (tool calls of a round start together). The steer then waits for
 * it, in steps of {@link STEER_WAIT_STEP_MS}, at most {@link STEER_MAX_WAITS}
 * times, and fails once the worker is gone or the budget is spent.
 */
export const STEER_WAIT_STEP_MS = 50;
export const STEER_MAX_WAITS = 600;

export type SteerWorkerArgs = { label: string; message: string };

/** `details` of a successful `steer_worker` result. */
export type SteerWorkerDetails = {
  label: string;
  steered: Array<{ conversationId: string; submissionId: string }>;
};

/** The label a (not yet started) subagent call was made with; "" when none. */
function callLabelOf(t: { input: unknown }): string {
  const label = (t.input as ToolTaskInput).call.arguments.label;
  return typeof label === "string" ? label.trim() : "";
}

export function createSteerWorkerTool(): ToolDefinition<SteerWorkerArgs> {
  return defineTool<SteerWorkerArgs>({
    name: STEER_WORKER_TOOL,
    description:
      "Send a message to a running subagent you started with a label. It reads the message at its next step. Use it to redirect or correct a worker that is still running.",
    parameters: z.object({
      label: z
        .string()
        .trim()
        .regex(/^[A-Za-z0-9_-]{1,64}$/, "label must be 1-64 characters of A-Z, a-z, 0-9, _ or -")
        .describe("The label the subagent call was started with."),
      message: z
        .string()
        .min(1)
        .max(STEER_MAX_CHARS)
        .describe("What the worker should know or change."),
    }),
    internal: true,
    enginePhase: {
      phase: "engine",
      run: (deps, task, rt, writeResult) => {
        const args = task.checkpoint?.arguments ?? {};
        const label = typeof args.label === "string" ? args.label.trim() : "";
        const message = typeof args.message === "string" ? args.message : "";
        const fail = (status: "error" | "invalid_arguments", content: string) =>
          rt.commit((tx) => writeResult(tx, task, status, { content, isError: true }, 0));
        if (label === "" || message === "") {
          return fail("invalid_arguments", "steer_worker needs a `label` and a `message`.");
        }
        // The calling run's own subagent calls: this conversation, this run.
        const own = rt.runtime
          .tasks({ conversationId: task.conversationId })
          .filter(
            (t) =>
              t.kind === "agent.tool" &&
              t.id !== task.id &&
              t.status !== "terminal" &&
              (t.input as ToolTaskInput).runId === task.input.runId,
          )
          .map((t) => ({ id: t.id, cp: t.checkpoint as ToolTaskCheckpoint }))
          .filter(
            (t): t is { id: string; cp: NonNullable<ToolTaskCheckpoint> } =>
              t.cp !== null && t.cp.subagent !== undefined && t.cp.label !== undefined,
          );
        // Same-round workers whose child is not created yet (their label is in the call).
        const starting = rt.runtime.tasks({ conversationId: task.conversationId }).filter((t) => {
          if (t.kind !== "agent.tool" || t.id === task.id || t.status === "terminal") return false;
          const input = t.input as ToolTaskInput;
          if (input.runId !== task.input.runId) return false;
          const cp = t.checkpoint as ToolTaskCheckpoint;
          if (cp?.subagent !== undefined) return false;
          return callLabelOf(t) !== "" && isSubagentTool(deps.tools.get(input.call.name));
        });
        const startingSame = starting.filter((t) => callLabelOf(t) === label);
        const waits = task.checkpoint?.steerWaits ?? 0;
        if (startingSame.length > 0 && waits < STEER_MAX_WAITS) {
          return rt.commit((tx) =>
            waiting({
              until: tx.now + STEER_WAIT_STEP_MS,
              phase: "engine",
              checkpoint: {
                arguments: args,
                ...(task.checkpoint?.label === undefined ? {} : { label: task.checkpoint.label }),
                steerWaits: waits + 1,
              },
            }),
          );
        }
        const targets = own.filter((t) => t.cp.label === label);
        const noMatch = () => {
          const running = own
            .filter((t) =>
              isBusy(rt.runtime, (t.cp.subagent as { conversationId: string }).conversationId),
            )
            .map((t) => t.cp.label as string);
          const starting_ = starting.map((t) => callLabelOf(t)).filter((l) => l !== "");
          const live = [...new Set([...running, ...starting_])].sort();
          return `No running worker has the label "${label}". ${
            live.length > 0
              ? `Running labels: ${live.join(", ")}.`
              : "No labelled worker is running."
          }`;
        };
        if (targets.length === 0) return fail("error", noMatch());
        const content = `${STEER_PREFIX}${message}`;
        const record: SubmissionInputRecord = {
          content,
          type: "input",
          whenBusy: "steer",
          untrusted: false,
        };
        return rt.commit(async (tx) => {
          // Reads first: a commit may not read tables after its first write.
          const plan = [];
          for (const t of targets) {
            const link = t.cp.subagent as NonNullable<typeof t.cp.subagent>;
            const child = await tx.getConversation(link.conversationId);
            const self = await tx.getConversation(task.conversationId);
            // Only a child this very tool task created, in the caller's tenant and project.
            if (
              !child ||
              !self ||
              child.ownerTaskId !== t.id ||
              child.tenantId !== self.tenantId ||
              child.projectId !== self.projectId
            ) {
              continue;
            }
            const requestId = `steer:${t.id}:${task.input.call.id}`;
            const existing = await tx.submissionByRequestId(child.id, requestId);
            // A new steer goes only to a child that is working right now.
            if (!existing && !isBusy(rt.runtime, child.id)) continue;
            const inbox = existing ? null : await Inbox.load(tx, child.id);
            plan.push({ childId: child.id, requestId, existing, inbox });
          }
          if (plan.length === 0) {
            return writeResult(tx, task, "error", { content: noMatch(), isError: true }, 0);
          }
          const steered: SteerWorkerDetails["steered"] = [];
          for (const p of plan) {
            if (p.existing || !p.inbox) {
              steered.push({ conversationId: p.childId, submissionId: p.existing?.id ?? "" });
              continue;
            }
            const submissionId = tx.mintId("submission");
            tx.createSubmission({
              id: submissionId,
              conversationId: p.childId,
              requestId: p.requestId,
              input: toSubmissionInput(record),
              status: "pending",
              result: null,
              taskId: null,
            });
            p.inbox.enqueue(submissionId, "steer", content, p.requestId);
            steered.push({ conversationId: p.childId, submissionId });
          }
          const details: SteerWorkerDetails = { label, steered };
          return writeResult(
            tx,
            task,
            "ok",
            {
              content: `Steered ${steered.length} worker${steered.length === 1 ? "" : "s"} labelled "${label}". It reads the message at its next step.`,
              details: details as unknown as JsonValue,
            },
            1,
          );
        });
      },
    },
    execute: () => ({
      content: "steer_worker runs only inside the agent loop.",
      isError: true,
    }),
  });
}
