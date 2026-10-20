/**
 * The native `ask_user` tool (R10): the model asks a person a question, with
 * optional choices, and the run waits durably for the answer. The tool never
 * executes; the tool task parks on a question (`questions.ts`) and the answer
 * becomes the result text. It is internal: its answer is the user's own input
 * and never taints the conversation.
 */
import { defineTool } from "../tools/registry.js";
import { questionSpecSchema } from "./questions.js";

export const ASK_USER_TOOL_NAME = "ask_user";

export const askUserTool = defineTool({
  name: ASK_USER_TOOL_NAME,
  description:
    "Ask the user a question and wait for their answer. Give a clear question and, when the answer is one of a few choices, the options (each with a short id and label). " +
    "Set multiSelect to let them choose several, and allowFreeText to let them type an answer instead. Use it only when you cannot proceed without their input.",
  parameters: questionSpecSchema,
  internal: true,
  humanInput: true,
  replay: "safe",
  execute: () => ({
    content: "ask_user is answered by a person and cannot be executed directly.",
    isError: true,
  }),
});
