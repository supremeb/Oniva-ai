/**
 * Time-ordered identifiers: a kind prefix followed by an RFC 9562 UUIDv7.
 *
 * Ordering within one millisecond uses a 42-bit counter spread across
 * `rand_a` (12 bits) and the top of `rand_b` (30 bits), seeded randomly per
 * millisecond and incremented per id. If the wall clock steps backwards the
 * generator keeps using its last timestamp, so ids from one generator always
 * sort in creation order.
 */
import { randomBytes } from "node:crypto";

export const ID_PREFIXES = Object.freeze({
  conversation: "conv_",
  entry: "ent_",
  task: "task_",
  submission: "subm_",
  approval: "appr_",
} as const);

export type IdKind = keyof typeof ID_PREFIXES;

const COUNTER_BITS = 42;
const COUNTER_MAX = 2 ** COUNTER_BITS - 1;
const LOW_SPLIT = 2 ** 30;
const UUID_V7_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export interface IdGenerator {
  next(kind: IdKind): string;
  uuid(): string;
  timestampOf(id: string): number;
}

export function createIdGenerator(options: { now?: () => number } = {}): IdGenerator {
  const now = options.now ?? Date.now;
  let lastMs = -1;
  let counter = 0;

  const uuid = (): string => {
    let ms = Math.floor(now());
    if (ms > lastMs) {
      lastMs = ms;
      // Seed in the lower half so there is plenty of room to increment.
      counter = randomBytes(6).readUIntBE(0, 6) % 2 ** (COUNTER_BITS - 1);
    } else {
      ms = lastMs;
      counter += 1;
      if (counter > COUNTER_MAX) {
        lastMs += 1;
        ms = lastMs;
        counter = 0;
      }
    }
    return formatUuidV7(ms, counter, randomBytes(4));
  };

  return {
    uuid,
    next: (kind) => ID_PREFIXES[kind] + uuid(),
    timestampOf: timestampOfId,
  };
}

function formatUuidV7(ms: number, counter: number, tail: Uint8Array): string {
  const hi = Math.floor(counter / LOW_SPLIT); // 12 bits -> rand_a
  const lo = counter % LOW_SPLIT; // 30 bits -> top of rand_b
  const b = new Uint8Array(16);
  const msHigh = Math.floor(ms / 2 ** 16);
  b[0] = (msHigh >>> 24) & 0xff;
  b[1] = (msHigh >>> 16) & 0xff;
  b[2] = (msHigh >>> 8) & 0xff;
  b[3] = msHigh & 0xff;
  b[4] = (ms >>> 8) & 0xff;
  b[5] = ms & 0xff;
  b[6] = 0x70 | ((hi >>> 8) & 0x0f);
  b[7] = hi & 0xff;
  b[8] = 0x80 | ((lo >>> 24) & 0x3f);
  b[9] = (lo >>> 16) & 0xff;
  b[10] = (lo >>> 8) & 0xff;
  b[11] = lo & 0xff;
  b.set(tail.subarray(0, 4), 12);
  const hex = Buffer.from(b).toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function stripPrefix(id: string): string {
  const kind = idKindOf(id);
  return kind === undefined ? id : id.slice(ID_PREFIXES[kind].length);
}

/** Millisecond timestamp embedded in a (possibly prefixed) UUIDv7. */
export function timestampOfId(id: string): number {
  const hex = stripPrefix(id).replaceAll("-", "").slice(0, 12);
  return Number.parseInt(hex, 16);
}

export function idKindOf(id: string): IdKind | undefined {
  for (const [kind, prefix] of Object.entries(ID_PREFIXES)) {
    if (id.startsWith(prefix)) return kind as IdKind;
  }
  return undefined;
}

export function isId(kind: IdKind, value: unknown): value is string {
  if (typeof value !== "string") return false;
  const prefix = ID_PREFIXES[kind];
  return value.startsWith(prefix) && UUID_V7_RE.test(value.slice(prefix.length));
}

const defaultGenerator = createIdGenerator();

/** Mint a new id of the given kind from the process-wide generator. */
export function newId(kind: IdKind): string {
  return defaultGenerator.next(kind);
}

/** Mint a bare UUIDv7 from the process-wide generator. */
export function uuidv7(): string {
  return defaultGenerator.uuid();
}
