"""Pure synthesis helpers shared by the legacy engine and the Deep Work gateway tools (U4, AE6)."""
from __future__ import annotations

from app.services.deep_work.models import Learning


def build_sources_section(learnings: list[Learning]) -> str:
    """Build deterministic sources section from learnings (deduplicated by URL, first-seen order)."""
    parts = ["\n## Sources"]
    seen_urls: dict[str, int] = {}
    for l in learnings:
        if l.source_url and l.source_url not in seen_urls:
            seen_urls[l.source_url] = len(seen_urls) + 1
            # Use source_title if available, otherwise extract a readable
            # title from the fact (first sentence, up to 120 chars).
            title = getattr(l, "source_title", None) or ""
            if not title:
                # Take first sentence or first 120 chars, whichever is shorter
                first_sentence = l.fact.split(". ")[0]
                title = first_sentence[:120]
            parts.append(
                f"{seen_urls[l.source_url]}. [{title}]({l.source_url})"
            )
    return "\n".join(parts)


def select_learnings(
    learnings: list[Learning], max_tokens: int = 15_000,
) -> list[Learning]:
    """Deduplicate and truncate learnings to fit within synthesizer context."""
    # 1. Dedup by fact prefix
    seen: set[str] = set()
    unique: list[Learning] = []
    for l in learnings:
        key = l.fact[:100].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(l)
    # 2. Sort by confidence, take top-N within token budget
    unique.sort(key=lambda l: l.confidence, reverse=True)
    selected: list[Learning] = []
    est_tokens = 0
    for l in unique:
        t = len(l.fact + (l.quote or "")) // 4
        if est_tokens + t > max_tokens:
            break
        selected.append(l)
        est_tokens += t
    return selected
