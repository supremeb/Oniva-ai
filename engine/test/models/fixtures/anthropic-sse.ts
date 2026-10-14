/**
 * Hand-built Messages API SSE fixtures, written from the documented event
 * shapes (message_start, content_block_start/delta/stop, message_delta,
 * message_stop, ping, error). No network recordings.
 */

type Frame = { type: string } & Record<string, unknown>;

export function sse(frames: Frame[]): string {
  return frames.map((f) => `event: ${f.type}\ndata: ${JSON.stringify(f)}\n\n`).join("");
}

function messageStart(model: string, usage: Record<string, number> = {}): Frame {
  return {
    type: "message_start",
    message: {
      id: "msg_fixture",
      type: "message",
      role: "assistant",
      model,
      content: [],
      stop_reason: null,
      stop_sequence: null,
      usage: {
        input_tokens: 25,
        output_tokens: 1,
        cache_creation_input_tokens: 0,
        cache_read_input_tokens: 0,
        ...usage,
      },
    },
  };
}

function messageDelta(stopReason: string, outputTokens: number): Frame {
  return {
    type: "message_delta",
    delta: { stop_reason: stopReason, stop_sequence: null },
    usage: { output_tokens: outputTokens },
  };
}

const textStart = (index: number): Frame => ({
  type: "content_block_start",
  index,
  content_block: { type: "text", text: "" },
});
const textDelta = (index: number, text: string): Frame => ({
  type: "content_block_delta",
  index,
  delta: { type: "text_delta", text },
});
const stop = (index: number): Frame => ({ type: "content_block_stop", index });

export const TEXT_FIXTURE = sse([
  messageStart("claude-opus-5-5", { cache_read_input_tokens: 100, cache_creation_input_tokens: 5 }),
  { type: "ping" },
  textStart(0),
  textDelta(0, "Hello"),
  textDelta(0, " world"),
  stop(0),
  messageDelta("end_turn", 12),
  { type: "message_stop" },
]);

export const THINKING_FIXTURE = sse([
  messageStart("claude-opus-5-5"),
  {
    type: "content_block_start",
    index: 0,
    content_block: { type: "thinking", thinking: "", signature: "" },
  },
  { type: "content_block_delta", index: 0, delta: { type: "thinking_delta", thinking: "Two " } },
  {
    type: "content_block_delta",
    index: 0,
    delta: { type: "thinking_delta", thinking: "plus two." },
  },
  {
    type: "content_block_delta",
    index: 0,
    delta: { type: "signature_delta", signature: "sig-abc" },
  },
  stop(0),
  {
    type: "content_block_start",
    index: 1,
    content_block: { type: "redacted_thinking", data: "opaque-blob" },
  },
  stop(1),
  textStart(2),
  textDelta(2, "4"),
  stop(2),
  messageDelta("end_turn", 30),
  { type: "message_stop" },
]);

export const TOOL_USE_FIXTURE = sse([
  messageStart("claude-opus-5-5"),
  textStart(0),
  textDelta(0, "Checking the weather."),
  stop(0),
  {
    type: "content_block_start",
    index: 1,
    content_block: { type: "tool_use", id: "toolu_01", name: "get_weather", input: {} },
  },
  {
    type: "content_block_delta",
    index: 1,
    delta: { type: "input_json_delta", partial_json: "" },
  },
  {
    type: "content_block_delta",
    index: 1,
    delta: { type: "input_json_delta", partial_json: '{"location": "Par' },
  },
  {
    type: "content_block_delta",
    index: 1,
    delta: { type: "input_json_delta", partial_json: 'is"}' },
  },
  stop(1),
  messageDelta("tool_use", 40),
  { type: "message_stop" },
]);

export const MAX_TOKENS_FIXTURE = sse([
  messageStart("claude-haiku-5-5"),
  textStart(0),
  textDelta(0, "The answer is"),
  stop(0),
  messageDelta("max_tokens", 5),
  { type: "message_stop" },
]);

export const WINDOW_EXCEEDED_FIXTURE = sse([
  messageStart("claude-opus-5-5"),
  textStart(0),
  textDelta(0, "Long"),
  stop(0),
  messageDelta("model_context_window_exceeded", 3),
  { type: "message_stop" },
]);

/** Text, then an in-stream `error` event (status 200 already sent). */
export const MID_STREAM_ERROR_FIXTURE = `${sse([
  messageStart("claude-opus-5-5"),
  textStart(0),
  textDelta(0, "Partial"),
])}event: error\ndata: ${JSON.stringify({
  type: "error",
  error: { type: "overloaded_error", message: "Overloaded" },
})}\n\n`;

/** The opening frames of a stream that then stalls (used for abort tests). */
export const STALL_PREFIX = sse([
  messageStart("claude-opus-5-5"),
  textStart(0),
  textDelta(0, "Thinking out lo"),
]);

export const PROMPT_TOO_LONG_BODY = {
  type: "error",
  error: {
    type: "invalid_request_error",
    message: "prompt is too long: 1048577 tokens > 1048576 maximum",
  },
};

export const RATE_LIMIT_BODY = {
  type: "error",
  error: {
    type: "rate_limit_error",
    message: "Number of request tokens has exceeded your rate limit",
  },
};
