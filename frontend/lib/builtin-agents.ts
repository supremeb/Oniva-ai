/**
 * Stable UUIDs for built-in agents.
 *
 * Zero-prefixed pattern makes them visually distinct from user-generated UUIDs
 * and avoids collisions. These are the canonical IDs used everywhere in the
 * frontend and accepted by the backend.
 */

export const BUILTIN_AGENT_UUIDS: Record<string, string> = {
  'general':          '00000000-0000-0000-0000-000000000001',
  'data-retrieval':   '00000000-0000-0000-0000-000000000002',
  'analysis':         '00000000-0000-0000-0000-000000000003',
  'compliance':       '00000000-0000-0000-0000-000000000004',
  'research':         '00000000-0000-0000-0000-000000000005',
  'kyc-aml':          '00000000-0000-0000-0000-000000000006',
  'market-abuse':     '00000000-0000-0000-0000-000000000007',
  'report-generator': '00000000-0000-0000-0000-000000000008',
};

/** Reverse map: UUID → slug */
const UUID_TO_SLUG: Record<string, string> = Object.fromEntries(
  Object.entries(BUILTIN_AGENT_UUIDS).map(([slug, uuid]) => [uuid, slug])
);

/** All built-in slugs */
const ALL_SLUGS = new Set(Object.keys(BUILTIN_AGENT_UUIDS));

/** All built-in UUIDs */
const ALL_UUIDS = new Set(Object.values(BUILTIN_AGENT_UUIDS));

/** Check if an ID (slug or UUID) belongs to a built-in agent */
export function isBuiltinAgentId(id: string): boolean {
  return ALL_SLUGS.has(id) || ALL_UUIDS.has(id);
}

/** Convert a slug to its stable UUID. Returns the input unchanged if already a UUID or unknown. */
export function slugToUUID(slug: string): string {
  return BUILTIN_AGENT_UUIDS[slug] ?? slug;
}

/** Convert a UUID to its slug. Returns the input unchanged if not a built-in UUID. */
export function uuidToSlug(uuid: string): string {
  return UUID_TO_SLUG[uuid] ?? uuid;
}
