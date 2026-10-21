"""Long-session tasks (U17): the turn plan, the generated replay fixtures and the fake-mode script.

A `long_session` task describes a conversation of 60 to 150 user turns in `input`:

    turns: 100               total user turns
    facts:                   things told to the assistant early, asked about again later
    - {id, kind: fact|id, statement, question, expect: [..]}
    probe_at: [0.5, 0.8, 1.0]  fractions of the session after which every fact is asked again (one turn per fact)
    pages / page_tokens / oversize_every / oversize_tokens   the web pages work turns open (see below)
    chat_every, recap_every  every Nth free turn is a plain chat turn / a "compare the last items" turn
    noisy: true              excluded from Gate B (high variance: padding, many oversize pages)

Turn kinds: `plant` (turns 1..F, one per fact), `work` (search the web for an item and open its page), `chat` (no
tool), `recap` (compare recent items; tempts the model to re-open a page whose result was cleared) and `probe`.

Work turns read pages that do not exist on the web: `write_fixtures` generates them as replay fixtures, so every arm
sees byte-identical tool results. A page is about `page_tokens` tokens; every `oversize_every`-th is `oversize_tokens`
(above the 8000-token offload threshold), which is what makes the tiers have something to do. The pages are generated
at stack boot into a scratch copy of the fixture directory; none is committed.

Fake mode answers each turn from a generated rule keyed on the `[tN]` marker in the user text. The scripted model
reports declared usage, not measured usage, so the rules declare a ramp (`fake.usage_base + n * fake.usage_per_turn`)
that stands for an unreduced conversation; it is what makes the engine's clear trigger fire. Graders therefore
estimate context size from the entries themselves in fake mode (`evals.graders.context`).
"""

from __future__ import annotations

import contextlib
import random
import shutil
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import replay
from .arms import split_arm
from .schema import Task

DEFAULT_TURNS = 100
DEFAULT_PROBE_AT = (0.5, 0.8, 1.0)
PAGE_HOST = "https://docs.example.test"
MARKER_FMT = "[t{n}]"
PAGE_MARK_FMT = "PAGEMARK-{n}#"
FAKE_USAGE_BASE = 4000
FAKE_USAGE_PER_TURN = 600
SECONDS_PER_TURN = {"fake": 8.0, "live": 75.0}

TOPICS = (
    "harbor crane", "tidal turbine", "grain silo", "rail signal", "ferry dock", "wind relay", "cold store",
    "water pump", "bridge cable", "solar array", "kiln burner", "lift motor", "weir gate", "freight lock",
    "radar mast", "tank valve", "belt drive", "cable duct", "salt press", "dock fender",
)
UNITS = ("kW", "tonnes", "m3 per hour", "bar", "units per day", "mm", "percent", "kN")
WORDS = (
    "the", "unit", "supports", "operators", "during", "scheduled", "maintenance", "windows", "and", "reports",
    "readings", "every", "shift", "while", "limits", "apply", "to", "peak", "load", "periods", "inspection",
    "covers", "bearings", "seals", "housing", "fasteners", "calibration", "records", "are", "kept", "for", "audit",
    "teams", "review", "variance", "against", "baseline", "output", "before", "approving", "changes", "spare",
    "parts", "ship", "from", "regional", "stores", "within", "two", "days", "of", "request", "site", "managers",
    "confirm", "access", "permits", "safety", "checks", "complete", "ahead", "restart", "procedure", "notes",
)
CHAT_PROMPTS = (
    "Give me a one-line status of where we are.",
    "Anything you would flag so far? Keep it to one sentence.",
    "Thanks. In one sentence, what is the pattern across the items?",
    "Is there any item that looked unusual to you? One sentence.",
    "Say in one sentence how confident you are in the figures so far.",
    "What would you check next if we had more time? One sentence.",
)


class SpecError(ValueError):
    """The task's `input` cannot describe a long session."""


@dataclass(frozen=True)
class Probe:
    id: str
    kind: str  # fact | id
    fact: str
    question: str
    expect: tuple[str, ...]
    plant_turn: int
    turn: int  # the 1-based turn that asks it


@dataclass(frozen=True)
class Turn:
    n: int  # 1-based
    kind: str  # plant | work | chat | recap | probe
    text: str
    probe: str | None = None
    query: str | None = None
    url: str | None = None


def base_id(task_id: str) -> str:
    return split_arm(task_id)[0]


def spec_turns(task: Task) -> int:
    n = int(task.input.get("turns") or DEFAULT_TURNS)
    if n < 8:
        raise SpecError(f"task '{task.id}': a long session needs at least 8 turns, got {n}")
    return n


def _facts(task: Task) -> list[dict[str, Any]]:
    raw = task.input.get("facts")
    if not isinstance(raw, list) or not raw:
        raise SpecError(f"task '{task.id}': long_session needs input.facts (early facts and IDs to ask about later)")
    out, seen = [], set()
    for f in raw:
        if not isinstance(f, dict) or not all(f.get(k) for k in ("id", "statement", "question", "expect")):
            raise SpecError(f"task '{task.id}': every fact needs id, statement, question and expect")
        if f["id"] in seen:
            raise SpecError(f"task '{task.id}': duplicate fact id '{f['id']}'")
        seen.add(f["id"])
        kind = f.get("kind", "fact")
        if kind not in ("fact", "id"):
            raise SpecError(f"task '{task.id}': fact '{f['id']}' kind must be fact or id")
        expect = f["expect"] if isinstance(f["expect"], list) else [f["expect"]]
        out.append({**f, "kind": kind, "expect": tuple(str(e) for e in expect)})
    return out


def _probe_rounds(n: int, n_facts: int, fractions: Sequence[float]) -> list[int]:
    """Last turn of each probe round (a round is `n_facts` consecutive turns), spaced and inside the session."""
    ends: list[int] = []
    floor = n_facts + 3  # something happens between planting and the first question
    for frac in sorted(fractions):
        end = min(n, max(round(frac * n), floor + n_facts))
        if ends and end < ends[-1] + n_facts + 2:
            continue
        ends.append(end)
    if not ends or ends[-1] != n:
        if ends and n < ends[-1] + n_facts + 2:
            ends[-1] = n
        else:
            ends.append(n)
    return ends


def plan(task: Task) -> tuple[list[Turn], list[Probe]]:
    """The session's user turns in order, and the probes among them. Deterministic."""
    n = spec_turns(task)
    facts = _facts(task)
    fractions = [float(x) for x in task.input.get("probe_at") or DEFAULT_PROBE_AT]
    ends = _probe_rounds(n, len(facts), fractions)
    chat_every = int(task.input.get("chat_every", 5))
    recap_every = int(task.input.get("recap_every", 12))
    turns: dict[int, Turn] = {}
    probes: list[Probe] = []
    for i, f in enumerate(facts, 1):
        turns[i] = Turn(i, "plant", f"Please remember this for the rest of our conversation: {f['statement']} "
                                    f'Just reply "noted". {MARKER_FMT.format(n=i)}')
    for end in ends:
        for j, f in enumerate(facts):
            t = end - len(facts) + 1 + j
            probes.append(Probe(f["id"], f["kind"], f["statement"], f["question"], f["expect"], facts.index(f) + 1, t))
            turns[t] = Turn(
                t, "probe",
                f"Quick check, from the start of our conversation: {f['question']} {MARKER_FMT.format(n=t)}",
                probe=f["id"],
            )
    slot = 0
    for t in range(1, n + 1):
        if t in turns:
            continue
        marker = MARKER_FMT.format(n=t)
        slot += 1
        if recap_every and slot >= 3 and slot % recap_every == 0:
            turns[t] = Turn(t, "recap", "Of the last three items we opened, which had the largest key figure? "
                                        f"Name the item and the figure. {marker}")
        elif chat_every and slot % chat_every == 0:
            turns[t] = Turn(t, "chat", f"{CHAT_PROMPTS[slot % len(CHAT_PROMPTS)]} {marker}")
        else:
            topic = TOPICS[t % len(TOPICS)]
            query = f"{topic} datasheet {t}"
            url = page_url(topic, t)
            turns[t] = Turn(
                t, "work",
                f'Item {t}: search the web for "{query}" and open {url}. Tell me the key figure in one sentence. {marker}',
                query=query, url=url,
            )
    return [turns[t] for t in range(1, n + 1)], probes


def page_url(topic: str, n: int) -> str:
    return f"{PAGE_HOST}/{topic.replace(' ', '-')}-{n}"


# --- generated replay fixtures ------------------------------------------------------------------------------------


def _page_tokens(task: Task, work_index: int, rng: random.Random) -> int:
    every = int(task.input.get("oversize_every", 4))
    if every and work_index % every == every - 1:
        return int(task.input.get("oversize_tokens", 12000))
    base = int(task.input.get("page_tokens", 3000))
    return int(base * rng.uniform(0.8, 1.2))


def page_text(task: Task, turn: Turn, tokens: int) -> str:
    rng = random.Random(f"{base_id(task.id)}:{turn.n}")
    figure = f"{rng.randint(100, 9999)} {rng.choice(UNITS)}"
    head = [
        PAGE_MARK_FMT.format(n=turn.n),
        f"Datasheet for item {turn.n} ({turn.query}).",
        f"Reference: REF-{turn.n:03d}-{rng.randint(1000, 9999)}",
        f"Key figure: {figure}",
        "",
    ]
    lines, chars = list(head), sum(len(x) + 1 for x in head)
    target = tokens * 4
    while chars < target:
        line = " ".join(rng.choice(WORDS) for _ in range(rng.randint(14, 22))).capitalize() + "."
        lines.append(line)
        chars += len(line) + 1
    return "\n".join(lines)


def write_fixtures(task: Task, directory: Path | str) -> int:
    """Write the search and page fixtures of every work turn of `task`; returns how many fixtures were written."""
    turns, _ = plan(task)
    count = 0
    work = [t for t in turns if t.kind == "work"]
    for idx, turn in enumerate(work):
        rng = random.Random(f"{base_id(task.id)}:size:{turn.n}")
        text = page_text(task, turn, _page_tokens(task, idx, rng))
        search_args = {"query": turn.query}
        browse_args = {"url": turn.url}
        search = {
            "query": " ".join(str(turn.query).lower().split()),
            "results": [{
                "title": f"Datasheet for item {turn.n}",
                "url": turn.url,
                "snippet": f"{PAGE_MARK_FMT.format(n=turn.n)} Datasheet and key figure for item {turn.n}.",
            }],
        }
        page = {"url": turn.url, "status": "ok", "content": text}
        replay.write_fixture(directory, replay.request_key("web_search", search_args), "web_search", search_args, search)
        replay.write_fixture(directory, replay.request_key("browse_url", browse_args), "browse_url", browse_args, page)
        count += 2
    return count


@contextlib.contextmanager
def replay_dir_for(tasks: Sequence[Task], base: Path | str) -> Iterator[Path]:
    """A scratch copy of the fixture directory `base` plus the generated pages of every long-session task in `tasks`."""
    with tempfile.TemporaryDirectory(prefix="oniva-ls-fixtures-") as scratch:
        directory = Path(scratch) / "web"
        if Path(base).is_dir():
            shutil.copytree(base, directory)
        else:
            directory.mkdir()
        done: set[str] = set()
        for task in tasks:
            if task.kind == "long_session" and base_id(task.id) not in done:
                done.add(base_id(task.id))
                write_fixtures(task, directory)
        yield directory


# --- fake mode -----------------------------------------------------------------------------------------------------


def declared_usage(task: Task, n: int) -> dict[str, int]:
    fake = task.fake or {}
    base = int(fake.get("usage_base", FAKE_USAGE_BASE))
    per_turn = int(fake.get("usage_per_turn", FAKE_USAGE_PER_TURN))
    return {"inputTokens": base + n * per_turn, "outputTokens": 60}


def fake_rules(task: Task) -> list[dict[str, Any]]:
    """Scripted-model rules for every turn of `task` (see the module docstring)."""
    turns, probes = plan(task)
    by_turn = {p.turn: p for p in probes}
    user_rules: list[dict[str, Any]] = []
    result_rules: list[dict[str, Any]] = []

    def text(n: int, body: str) -> dict[str, Any]:
        return {"steps": [{"type": "text", "text": body}], "usage": declared_usage(task, n)}

    for t in turns:
        when = {"role": "user", "includes": MARKER_FMT.format(n=t.n)}
        if t.kind == "plant":
            resp = text(t.n, "Noted.")
        elif t.kind == "probe":
            resp = text(t.n, f"From earlier in our conversation: {by_turn[t.n].expect[0]}.")
        elif t.kind == "recap":
            resp = text(t.n, "Going by what I noted earlier, the last three items were close; the largest is the last one.")
        elif t.kind == "chat":
            resp = text(t.n, "Everything is on track so far.")
        else:
            resp = {
                "steps": [
                    {"type": "tool_call", "name": "web_search", "arguments": {"query": t.query}},
                    {"type": "tool_call", "name": "browse_url", "arguments": {"url": t.url}},
                ],
                "usage": declared_usage(task, t.n),
            }
            result_rules.append({
                "when": {"role": "tool_result", "includes": PAGE_MARK_FMT.format(n=t.n)},
                "respond": text(t.n, f"Item {t.n}: noted, the datasheet lists the key figure."),
            })
        user_rules.append({"when": when, "respond": resp})
    return user_rules + result_rules


def timeout_s(task: Task, mode: str) -> float:
    return 120.0 + spec_turns(task) * SECONDS_PER_TURN[mode]
