"""Turn a Claude Code session JSONL into a compact session card.

Tier 0: deterministic, zero tokens. The job here is *discarding*, not converting — a naive
dump of a transcript is mostly `tool_result` noise, and `parentUuid` makes a session a tree
rather than a transcript, so a flat read replays work that was abandoned by a rewind. So we
walk to the active leaf, drop the dead branches, keep the intent and the actions, and emit a
card that is roughly a tenth the size with nearly all the signal.

See docs/adr/0002-free-capture-paid-synthesis.md, docs/adr/0004-deterministic-cli.md, and
the "session parser" section of docs/architecture.md. D3 (the Stop hook) wires this to a live
session; D7 (Codex) reuses the card model with a different front-end.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from crate_wiki import vault

SOURCE = "claude-code"

# Where a card lands inside a vault, relative to its root.
CARD_DIR = ("raw", "sessions", "claude-code")

# The record types that are actual conversation. Everything else a session file carries —
# titles, queue operations, attachments, system notes — is bookkeeping the card discards.
CONVERSATION = ("user", "assistant")

# Tools whose calls are "files touched", mapped to the input key holding the path.
FILE_TOOLS = {
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}


# --------------------------------------------------------------------------------------
# the card model
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    """One thing the assistant did that survives into the card.

    `kind` is "edit", "command", or "subagent"; `value` is the file path, the command, or the
    subagent label. Everything else a `tool_use` might be (a read, a grep) is noise and never
    becomes an Action.
    """

    kind: str
    value: str


@dataclass
class Turn:
    """A user prompt or an assistant step on the live path."""

    role: str  # "user" | "assistant"
    time: str  # "14:03", or "" when the timestamp is unreadable
    text: str
    actions: list[Action] = field(default_factory=list)


@dataclass
class Card:
    """A parsed session, ready to render and to record in the capture cursor."""

    session_id: str
    turns: list[Turn]
    started: str  # ISO timestamp, or ""
    ended: str
    cwd: str
    git_branch: str
    cc_version: str  # the Claude Code `version` field
    crate_version: str
    last_uuid: str  # the live leaf — the idempotency key for re-runs
    records: int  # total records in the file, informational

    # --- derived rollup (all deterministic) -------------------------------------------

    @property
    def files(self) -> list[str]:
        seen = {a.value for t in self.turns for a in t.actions if a.kind == "edit"}
        return sorted(seen)

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

    def filename(self) -> str:
        return f"{self.date}-{_short(self.session_id)}.md"

    def render(self) -> str:
        return _render_card(self)


@dataclass(frozen=True)
class CaptureResult:
    session_id: str
    card_path: Path
    written: bool  # False means the card already reflected this session — nothing to do


# --------------------------------------------------------------------------------------
# parsing the tree
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


def _live_path(records: list[dict]) -> list[dict]:
    """The surviving conversation: walk from the active leaf back to its root.

    Rewinds branch the tree, so the live conversation is the path the session actually ended
    on. The leaf is the *last conversational turn*, not the last physical line: Claude appends
    bookkeeping records (titles, queue ops) after the exchange, and they carry no `parentUuid`,
    so walking from one yields a one-record "session" of pure metadata. From the real leaf we
    follow `parentUuid` up; a parent that isn't in the file (a compaction boundary) ends the
    walk cleanly. Sidechains are excluded here — they collapse to one line each.
    """
    main = [r for r in records if not r.get("isSidechain")]
    by_uuid = {r["uuid"]: r for r in main if "uuid" in r}

    leaf = next((r for r in reversed(main) if r.get("type") in CONVERSATION), None)
    if leaf is None:
        return []

    path: list[dict] = []
    seen: set[str] = set()
    current: dict | None = leaf
    while current is not None:
        uuid = current.get("uuid")
        if uuid in seen:  # a cycle can only come from corrupt data; stop rather than loop
            break
        if uuid is not None:
            seen.add(uuid)
        path.append(current)
        parent = current.get("parentUuid")
        current = by_uuid.get(parent) if parent is not None else None

    path.reverse()
    return path


# --------------------------------------------------------------------------------------
# extracting what to keep
# --------------------------------------------------------------------------------------


def _blocks(record: dict) -> list:
    """The content blocks of a record, normalising the string-or-list shape to a list."""
    message = record.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return content
    return []


def _user_text(record: dict) -> str | None:
    """A genuine user prompt, verbatim — or None if this "user" record isn't one.

    A record with `role: user` is often a tool_result being handed back, not something a human
    typed; those carry no text block, so they fall out here. Meta records (command wrappers,
    system notes) are dropped outright.
    """
    if record.get("isMeta"):
        return None
    parts = [
        block.get("text", "")
        for block in _blocks(record)
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    text = "\n".join(part for part in parts if part).strip()
    return text or None


def _assistant(record: dict) -> tuple[str, list[Action]]:
    """The assistant's prose and its surviving actions. `thinking` blocks are dropped."""
    prose: list[str] = []
    actions: list[Action] = []
    for block in _blocks(record):
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text = block.get("text", "").strip()
            if text:
                prose.append(text)
        elif kind == "tool_use":
            action = _action(block)
            if action is not None:
                actions.append(action)
        # "thinking" and anything else: dropped.
    return "\n\n".join(prose), actions


def _action(block: dict) -> Action | None:
    """A `tool_use` block as an Action, or None when the tool is noise (a read, a grep)."""
    name = block.get("name", "")
    params = block.get("input")
    if not isinstance(params, dict):
        params = {}

    if name in FILE_TOOLS:
        target = params.get(FILE_TOOLS[name])
        return Action("edit", str(target)) if target else None
    if name == "Bash":
        command = str(params.get("command", "")).strip()
        return Action("command", _oneline(command)) if command else None
    if name == "Task":
        label = params.get("subagent_type") or params.get("description") or "subagent"
        return Action("subagent", str(label))
    return None


def _turns(live: list[dict]) -> list[Turn]:
    """Live-path records as turns, with consecutive assistant steps folded into one.

    Claude emits several assistant records per reply (text, then a tool call, then more text
    after the result). Between them sit tool_result "user" records, which drop out as noise —
    leaving assistant steps adjacent. Folding them keeps the card reading like a conversation.
    """
    turns: list[Turn] = []
    for record in live:
        rtype = record.get("type")
        time = _hhmm(record.get("timestamp"))
        if rtype == "user":
            text = _user_text(record)
            if text:
                turns.append(Turn("user", time, text))
        elif rtype == "assistant":
            prose, actions = _assistant(record)
            if not prose and not actions:
                continue
            if turns and turns[-1].role == "assistant":
                prev = turns[-1]
                prev.text = "\n\n".join(part for part in (prev.text, prose) if part)
                prev.actions.extend(actions)
            else:
                turns.append(Turn("assistant", time, prose, actions))
    return turns


def parse(session: Path, *, crate_version: str) -> Card | None:
    """Parse a session JSONL into a Card, or None if there's nothing usable in it."""
    records = _load_records(session)
    live = _live_path(records)
    if not live:
        return None

    turns = _turns(live)
    timestamps = [r["timestamp"] for r in live if r.get("timestamp")]

    return Card(
        session_id=_last(live, "sessionId"),
        turns=turns,
        started=min(timestamps) if timestamps else "",
        ended=max(timestamps) if timestamps else "",
        cwd=_last(live, "cwd"),
        git_branch=_last(live, "gitBranch"),
        cc_version=_last(live, "version"),
        crate_version=crate_version,
        last_uuid=live[-1].get("uuid", ""),
        records=len(records),
    )


# --------------------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------------------


def _render_card(card: Card) -> str:
    files = ", ".join(card.files)
    lines = [
        "---",
        f"source: {SOURCE}",
        f"session_id: {card.session_id}",
        f"started: {card.started}",
        f"ended: {card.ended}",
        f"duration_min: {card.duration_min}",
        f"cwd: {card.cwd}",
        f"git_branch: {card.git_branch}",
        f"cc_version: {card.cc_version}",
        f"crate_version: {card.crate_version}",
        f"files: [{files}]",
        f"commands: {card.command_count}",
        f"subagents: {card.subagent_count}",
        "---",
        "",
        f"# {card.title} · {card.date}",
    ]

    for turn in card.turns:
        lines.append("")
        stamp = f" · {turn.time}" if turn.time else ""
        if turn.role == "user":
            lines.append(f"**user**{stamp}")
            lines += [f"> {line}" for line in turn.text.splitlines()] or ["> "]
        else:
            head = f"**assistant** — {turn.text}" if turn.text else "**assistant**"
            lines.append(head)
            for action in turn.actions:
                lines.append(_render_action(action))

    return "\n".join(lines) + "\n"


def _render_action(action: Action) -> str:
    if action.kind == "command":
        return f"- run `{action.value}`"
    if action.kind == "subagent":
        return f"- subagent ({action.value})"
    return f"- edit {action.value}"


# --------------------------------------------------------------------------------------
# capture — write the card, idempotently
# --------------------------------------------------------------------------------------


def capture(session: Path, vault_path: Path, *, crate_version: str) -> CaptureResult:
    """Parse `session` and write its card into the vault, unless it's already captured.

    Idempotent: the capture cursor in `.crate/state.json` records the live leaf's uuid per
    session. A re-run with no new records writes nothing; a resumed session (new records, new
    leaf) re-renders its one card. Raises VaultError on anything the user must fix.
    """
    vault_path = vault_path.expanduser().resolve()
    vault.load_config(vault_path)  # raises VaultError when it isn't a vault
    if not session.is_file():
        raise vault.VaultError(f"no such session file: {session}")

    card = parse(session, crate_version=crate_version)
    if card is None:
        raise vault.VaultError(f"no usable records in {session}")

    state_path = vault_path / ".crate" / "state.json"
    state = _load_state(state_path)
    source_state = state.setdefault(SOURCE, {})
    prior = source_state.get(card.session_id)

    card_path = vault_path.joinpath(*CARD_DIR) / card.filename()
    if prior and prior.get("last_uuid") == card.last_uuid and card_path.is_file():
        return CaptureResult(card.session_id, card_path, written=False)

    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(card.render(), encoding="utf-8")

    source_state[card.session_id] = {"last_uuid": card.last_uuid, "records": card.records}
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
# small helpers
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
    return session_id[:8] if session_id else "unknown"


def _oneline(text: str) -> str:
    """Collapse a command to a single line so it renders as one list item."""
    return " ".join(text.split())


def _parse_ts(timestamp: str) -> datetime | None:
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hhmm(timestamp: str | None) -> str:
    parsed = _parse_ts(timestamp) if timestamp else None
    return parsed.strftime("%H:%M") if parsed else ""
