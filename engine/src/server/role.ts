/**
 * Session roles (sharding). One worker serves one session; a deployment may run several sessions side by
 * side so that interactive conversations (chat, Deep Work) and background runs (workflow, Fleet, batch)
 * never share a session's commit line. The role says which lane of work a worker's session is for:
 *
 * - `all` (default): no restriction; the single-worker behaviour.
 * - `interactive`: only `interactive`-lane conversations; the workflow task kinds are not registered.
 * - `background`: only `background`-lane conversations.
 *
 * The role is a guard against misrouting, not the router: the backend decides which session a conversation
 * lives in, once, when it creates it. A conversation that exists in one session cannot be created in
 * another (ids are unique across sessions in storage), so it has exactly one owner.
 */
import type { Lane } from "../tasks/define.js";

export type SessionRole = "all" | "interactive" | "background";

export const SESSION_ROLES: readonly SessionRole[] = Object.freeze([
  "all",
  "interactive",
  "background",
]);

/** Whether a session with `role` may hold a conversation whose runs go in `lane`. */
export function roleAllowsLane(role: SessionRole, lane: Lane): boolean {
  return role === "all" || role === lane;
}
