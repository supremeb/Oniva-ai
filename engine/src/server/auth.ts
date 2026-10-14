/**
 * Request authentication and tenant binding (R19, KTD12).
 *
 * The backend calls the engine with a bearer service token, compared in
 * constant time (both sides are hashed first, so neither the content nor the
 * length of the expected token leaks through timing). It passes the tenant
 * and project in headers, checked against the conversation's stored binding,
 * and the principal it verified on approval decisions.
 */
import { createHash, timingSafeEqual } from "node:crypto";
import type { IncomingMessage } from "node:http";
import type { ConversationRecord } from "../storage/types.js";

export const ONIVA_HEADERS = Object.freeze({
  tenant: "x-oniva-tenant",
  project: "x-oniva-project",
  principal: "x-oniva-principal",
});

const digest = (value: string): Buffer => createHash("sha256").update(value, "utf8").digest();

/** Whether an `Authorization` header carries `Bearer <expected>`. Constant time. */
export function bearerMatches(header: string | undefined, expected: string): boolean {
  const match = /^Bearer[ \t]+(\S+)[ \t]*$/i.exec(header ?? "");
  const given = match?.[1] ?? "";
  // Always compare, even for a malformed header, so timing does not tell them apart.
  const same = timingSafeEqual(digest(given), digest(expected));
  return match !== null && same;
}

/** A single, non-empty header value, or null. */
export function headerValue(req: IncomingMessage, name: string): string | null {
  const raw = req.headers[name];
  const value = Array.isArray(raw) ? (raw.length === 1 ? raw[0] : undefined) : raw;
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length === 0 ? null : trimmed;
}

export interface TenantBinding {
  tenantId: string;
  projectId: string;
}

/** Whether a conversation is bound to the caller's tenant and project. */
export function bindingMatches(conversation: ConversationRecord, caller: TenantBinding): boolean {
  return conversation.tenantId === caller.tenantId && conversation.projectId === caller.projectId;
}
