"""The session card — the shared model, renderer, and capture cursor every source feeds.

Tier 0 is deterministic and free (ADR-0002, ADR-0004): a session becomes a compact card whose
job is *discarding* noise, not converting a transcript. The shape of a card — a user's intent,
the assistant's prose, the files touched and commands run, the timings — is the same whatever
tool produced the session. What differs is the on-disk format, and that lives in a per-source
*adapter* (`claude.py`, `codex.py`), each a module exposing `SOURCE` and a
`parse(session, *, crate_version) -> list[Card]`. This module owns everything downstream of a
parsed `Card`: rendering, and the idempotent capture cursor. See
docs/adr/0014-shared-card-core-per-source-adapters.md and
docs/adr/0015-a-day-of-a-thread-is-a-card.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from crate_wiki import vault

# --------------------------------------------------------------------------------------
# the card model
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    """One thing the assistant did that survives into the card.

    `kind` is "edit", "command", or "subagent"; `value` is the file path, the command, or the
    subagent label. Everything else a tool call might be (a read, a grep) is noise and never
    becomes an Action.
    """

    kind: str
    value: str


@dataclass
class Turn:
    """A user prompt, or an assistant step, on the live path.

    `items` is an ordered stream: a user turn holds a single prose string; an assistant turn
    holds prose strings and Actions interleaved in the order they occurred — so the card shows
    which command followed which reasoning, rather than a wall of prose then a wall of actions.
    """

    role: str  # "user" | "assistant"
    time: str  # "14:03", or "" when the timestamp is unreadable
    items: list = field(default_factory=list)

    @property
    def actions(self) -> list[Action]:
        return [item for item in self.items if isinstance(item, Action)]

    @property
    def prose(self) -> str:
        return "\n\n".join(item for item in self.items if isinstance(item, str))


@dataclass(frozen=True)
class Usage:
    """The token usage a card's day of work spent — the billing basis, kept per category.

    Each category prices differently on a metered API, so they stay separate rather than folded
    into one total: `input` and `output` are the uncached prompt and completion tokens, and
    `cache_read`/`cache_write` are the cached-prompt and cache-creation tokens. Normalised to be
    **disjoint** across both sources — `input` never includes what `cache_read` counts — so a
    downstream cost calc is `input·rate_in + output·rate_out + …` with no double-counting,
    whichever tool produced the session. `model` is the model the day's work ran on (the last one
    seen, when a day mixed more than one); it's what selects the rate table. Every field is a
    deterministic sum over the day's records — no judgment, no pricing — so this belongs in the
    free tier (ADR-0002, ADR-0004). The dollar figure is derived outside the engine, from these.
    """

    input: int
    output: int
    cache_read: int
    cache_write: int
    model: str

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write


@dataclass
class Card:
    """A parsed session, ready to render and to record in the capture cursor.

    `source` is the adapter that produced it ("claude-code", "codex"); it names both the
    frontmatter and the vault directory the card lands in, so two sources never collide.
    `cursor` is the idempotency token — whatever value, stable per session, tells a re-run
    "nothing new here" (a live-leaf uuid for a tree format, a record count for an append log).
    """

    source: str
    session_id: str
    turns: list[Turn]
    started: str  # ISO timestamp, or ""
    ended: str
    cwd: str
    git_branch: str
    tool_version: str  # the version of the tool that produced the session
    crate_version: str
    cursor: str  # the idempotency token for re-runs — see the class docstring
    records: int  # total records in the file, informational
    # The day's token spend, or None when the source carried no usable usage data (an older
    # session file, a Codex rollout with no telemetry). None renders as absent frontmatter, so a
    # card without it is byte-identical to one from before usage existed.
    usage: Usage | None = None

    # --- derived rollup (all deterministic) -------------------------------------------

    @property
    def files(self) -> list[str]:
        seen = {a.value for t in self.turns for a in t.actions if a.kind == "edit"}
        return sorted(_relative(path, self.cwd) for path in seen)

    @property
    def command_count(self) -> int:
        return sum(1 for t in self.turns for a in t.actions if a.kind == "command")

    @property
    def subagent_count(self) -> int:
        return sum(1 for t in self.turns for a in t.actions if a.kind == "subagent")

    @property
    def duration_min(self) -> int:
        start, end = _parse_ts(self.started), _parse_ts(self.ended)
        if start is None or end is None:
            return 0
        return round((end - start).total_seconds() / 60)

    @property
    def date(self) -> str:
        return self.started[:10] if self.started else "undated"

    @property
    def title(self) -> str:
        if self.git_branch:
            return self.git_branch
        if self.cwd:
            return Path(self.cwd).name
        return _short(self.session_id)

    @property
    def state_key(self) -> str:
        """This card's key in the capture cursor — the session *and* the day it covers.

        A session id alone was enough while one session file meant one card. A Codex thread
        resumed across days now yields one card per active day (ADR-0015), all sharing a session
        id and differing only by date, so the id alone would collide and each day would silently
        overwrite the last one's cursor. Same composite, and for the same reason, as `filename()`.
        """
        return f"{self.session_id}:{self.date}"

    def filename(self) -> str:
        return f"{self.date}-{_slug(self.session_id)}.md"

    def render(self) -> str:
        return _render_card(self)


@dataclass(frozen=True)
class CaptureResult:
    session_id: str
    card_path: Path
    written: bool  # False means the card already reflected this session — nothing to do


# --------------------------------------------------------------------------------------
# loading records — the one shape both formats share: JSONL, one object per line
# --------------------------------------------------------------------------------------


def _load_records(path: Path) -> list[dict]:
    """Every JSON object in the file, in order. Malformed lines are skipped, not fatal.

    Capture has to be robust: a half-written final line (the session was still going) must
    not lose the whole card. See ADR-0002 — capture fails quietly, it never raises.
    """
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


# --------------------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------------------


# A reproduced prose paragraph can carry an inline markdown link the model wrote — often a
# relative path to a code file it touched. Obsidian resolves such a target vault-locally, and
# since no note matches, clicking the Graph-view node *creates* a blank `<path>.md` phantom. A
# card describes another repo, so a vault-relative target never resolves by construction; render
# it as an inert code span. Real URLs (they open a browser, not a vault node) are left clickable.
_MD_LINK = re.compile(r"!?\[([^\]]+)\]\(([^)]+)\)")
_URL_TARGET = re.compile(r"^\s*<?\s*(?:[a-z][a-z0-9+.\-]*://|mailto:|//|www\.|#)", re.IGNORECASE)


def _inert_local_links(text: str) -> str:
    """Turn markdown links with local (non-URL) targets into `code spans`, leaving URLs alone."""

    def replace(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        if _URL_TARGET.match(target):
            return match.group(0)
        return f"`{label}`"

    return _MD_LINK.sub(replace, text)


def _render_card(card: Card) -> str:
    files = ", ".join(card.files)
    lines = [
        "---",
        f"source: {card.source}",
        f"session_id: {card.session_id}",
        f"started: {card.started}",
        f"ended: {card.ended}",
        f"duration_min: {card.duration_min}",
        f"cwd: {card.cwd}",
        f"git_branch: {card.git_branch}",
        f"tool_version: {card.tool_version}",
        f"crate_version: {card.crate_version}",
        f"files: [{files}]",
        f"commands: {card.command_count}",
        f"subagents: {card.subagent_count}",
    ]

    if card.usage is not None:
        # Flat scalar keys, not a nested map: the external cost-per-client reader (the engine
        # doesn't do dollars — ADR-0002) parses these straight out of the frontmatter, and every
        # existing key here is already flat. `model` selects the rate; the four counts are disjoint
        # (see Usage) so a sum across cards never double-counts a cached token.
        lines += [
            f"model: {card.usage.model}",
            f"input_tokens: {card.usage.input}",
            f"output_tokens: {card.usage.output}",
            f"cache_read_tokens: {card.usage.cache_read}",
            f"cache_write_tokens: {card.usage.cache_write}",
        ]

    lines += [
        "---",
        "",
        f"# {card.title} · {card.date}",
    ]

    for turn in card.turns:
        lines.append("")
        stamp = f" · {turn.time}" if turn.time else ""
        if turn.role == "user":
            prompt = _inert_local_links(turn.items[0]) if turn.items else ""
            lines.append(f"**user**{stamp}")
            lines += [f"> {line}" for line in prompt.splitlines()] or ["> "]
        else:
            lines += _render_assistant(turn, card.cwd)

    return "\n".join(lines) + "\n"


def _render_assistant(turn: Turn, cwd: str) -> list[str]:
    """An assistant turn, its prose and actions interleaved in the order they happened."""
    items = turn.items
    start = 0
    if items and isinstance(items[0], str):
        lines = [f"**assistant** — {_inert_local_links(items[0])}"]  # lead with first prose, inline
        start = 1
    else:
        lines = ["**assistant**"]
    for item in items[start:]:
        if isinstance(item, Action):
            lines.append(_render_action(item, cwd))
        else:
            # a later prose paragraph, set off by a blank line
            lines += ["", _inert_local_links(item)]
    return lines


def _render_action(action: Action, cwd: str) -> str:
    if action.kind == "command":
        return f"- run `{action.value}`"
    if action.kind == "subagent":
        return f"- subagent ({action.value})"
    return f"- edit {_relative(action.value, cwd)}"


# --------------------------------------------------------------------------------------
# capture — write the card, idempotently
# --------------------------------------------------------------------------------------


def capture(parse, session: Path, vault_path: Path, *, crate_version: str) -> list[CaptureResult]:
    """Validate `session` and the vault, parse with `parse`, and write every card it yields.

    `parse` is a source adapter's `parse(session, *, crate_version) -> list[Card]`; everything
    from a parsed Card on is source-agnostic, and lives in `write`. One session file usually
    means one card, but a Codex thread resumed across days is one card per day it was active on
    (ADR-0015) — so the result is a list, in the same order the adapter returned, oldest first.
    Raises VaultError on anything the user must fix: a bad vault, a missing session file, or a
    session with nothing usable in it. See `write` for the idempotent part of the contract.
    """
    vault_path = vault_path.expanduser().resolve()
    vault.load_config(vault_path)  # raises VaultError when it isn't a vault
    if not session.is_file():
        raise vault.VaultError(f"no such session file: {session}")

    parsed = parse(session, crate_version=crate_version)
    if not parsed:
        raise vault.VaultError(f"no usable records in {session}")

    return [write(card, vault_path) for card in parsed]


def write(card: Card, vault_path: Path) -> CaptureResult:
    """Write `card` into `vault_path` unless it's already captured. `vault_path` must already be
    a resolved, validated vault — this does no validation of its own, so a caller sweeping many
    cards into one vault (`codex.capture_all`) can validate once and call this per card.

    Idempotent: the cursor in `.crate/state.json` is keyed by the card's source and its
    `state_key` (session id *and* day), so a Codex capture never touches Claude's cursor, and two
    days of one resumed thread never overwrite each other's. A re-run with an unchanged cursor
    writes nothing at all — the card isn't even opened; a session with new records re-renders the
    card that day's records belong to, and only that one.
    """
    state_path = vault_path / ".crate" / "state.json"
    state = _load_state(state_path)
    source_state = state.setdefault(card.source, {})
    prior = source_state.get(card.state_key)

    card_path = vault_path / "raw" / "sessions" / card.source / card.filename()
    if prior and prior.get("cursor") == card.cursor and card_path.is_file():
        return CaptureResult(card.session_id, card_path, written=False)

    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(card.render(), encoding="utf-8")

    source_state[card.state_key] = {"cursor": card.cursor, "records": card.records}
    _write_state(state_path, state)
    return CaptureResult(card.session_id, card_path, written=True)


def _load_state(path: Path) -> dict:
    """The capture cursor, or an empty one. A corrupt cursor is rebuilt, not fatal."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------------------
# small helpers — shared by every adapter
# --------------------------------------------------------------------------------------


def _last(records: list[dict], key: str) -> str:
    """The last non-empty value of `key` across records — session metadata can shift mid-run
    (a branch switch, a version bump), and the final state is the one worth recording."""
    value = ""
    for record in records:
        candidate = record.get(key)
        if candidate:
            value = str(candidate)
    return value


def _short(session_id: str) -> str:
    """A truncated id for a human-facing label (a card title) — collisions there are cosmetic."""
    return session_id[:8] if session_id else "unknown"


def _slug(session_id: str) -> str:
    """The full session id, made filesystem-safe, for the card filename.

    Never truncated: it's the filename's only guarantee that two sessions don't collide onto one
    card and overwrite each other. Codex ids are time-ordered UUIDv7 whose leading hex repeats
    for sessions started within the same ~minute, so any fixed-length prefix would clash — only
    the whole id is unique by construction. Claude's random uuids are safe either way; keeping
    one rule for both sources keeps the seam clean (ADR-0014).
    """
    safe = re.sub(r"[^A-Za-z0-9._-]", "", session_id)
    return safe or "unknown"


def _relative(path: str, cwd: str) -> str:
    """A file path relative to the session's cwd when it sits inside it; absolute otherwise.

    Paths outside the working tree — a plan file under ~/.claude, say — stay absolute: they are
    genuinely elsewhere, and a trail of `../../..` would be less honest than the full path.
    """
    if not cwd:
        return path
    try:
        return str(Path(path).relative_to(cwd))
    except ValueError:
        return path


def _oneline(text: str) -> str:
    """Collapse a command to a single line so it renders as one list item."""
    return " ".join(text.split())


def _int(value) -> int:
    """A token count coerced to a non-negative int, or 0 for anything unreadable.

    Usage numbers come straight from another tool's JSON, where a field can be missing, null, or
    (rarely) a float. Capture is fail-quiet (ADR-0002): a bad count contributes 0 rather than
    aborting the card. Negatives are clamped — a token count is never negative, and letting one
    through would silently understate a day's spend in the external cost sum.
    """
    if isinstance(value, bool):  # bool is an int subclass; a flag is not a count
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    return 0


def _parse_ts(timestamp: str) -> datetime | None:
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _local(timestamp: str) -> str:
    """`timestamp` (UTC-or-any-offset ISO-8601) as the local wall-clock time it happened at.

    Capture always runs on the machine that owns the session, so its local timezone is the
    wall clock that matters. Guarded like `_parse_ts`: unparseable, or a naive (tzinfo-less)
    string — no offset to convert from — is returned unchanged. Capture is fail-quiet
    (ADR-0002): a bad timestamp degrades the card, it never aborts the write.
    """
    parsed = _parse_ts(timestamp)
    if parsed is None or parsed.tzinfo is None:
        return timestamp
    return parsed.astimezone().isoformat()


def _hhmm(timestamp: str | None) -> str:
    parsed = _parse_ts(_local(timestamp)) if timestamp else None
    return parsed.strftime("%H:%M") if parsed else ""


def _day_key(timestamp, previous: str) -> str:
    """The local calendar day a record belongs to, or `previous` when it can't be read.

    Local wall clock, not UTC — the day boundary is the one the person who did the work lived
    (ADR-0013), and `_local` above is the same chokepoint `started`/`ended` and every turn stamp
    already go through. A record whose timestamp is missing or unparseable inherits the day of the
    record before it: records reach here in the order they happened, so the neighbour is the best
    available answer, and it keeps a session of unreadable timestamps as one card rather than
    fragmenting it. Fail-quiet (ADR-0002) — nothing here raises on a bad value.

    This lives in the core rather than in an adapter because it is a rule about a *timestamp*, not
    about a record shape: both sources split a session by day (ADR-0015, ADR-0016) and both must
    draw the boundary in the same place. What stays per-adapter is the grouping itself — which
    records are eligible, and what else each day has to carry (ADR-0014's seam).
    """
    if not isinstance(timestamp, str) or not timestamp:
        return previous
    local = _local(timestamp)
    return local[:10] if _parse_ts(local) is not None else previous
