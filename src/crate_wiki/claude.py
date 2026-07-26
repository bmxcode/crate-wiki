"""Turn a Claude Code session JSONL into a session card — the Claude source adapter.

Tier 0: deterministic, zero tokens. The job here is *discarding*, not converting — a naive
dump of a transcript is mostly `tool_result` noise, and `parentUuid` makes a session a tree
rather than a transcript, so a flat read replays work that was abandoned by a rewind. So we
walk to the active leaf, drop the dead branches, keep the intent and the actions, and hand a
`Card` to `cards.capture` — which owns everything downstream, shared with every other source.

See docs/adr/0002-free-capture-paid-synthesis.md, docs/adr/0004-deterministic-cli.md,
docs/adr/0014-shared-card-core-per-source-adapters.md, and the "session parser" section of
docs/architecture.md.
"""

from __future__ import annotations

from pathlib import Path

from crate_wiki import cards
from crate_wiki.cards import Action, Card, Turn

SOURCE = "claude-code"

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
# parsing the tree
# --------------------------------------------------------------------------------------


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


# Wrappers the harness injects into user-role records — task notifications, system reminders,
# slash-command scaffolding, captured command output. None of it was typed by a human, so it
# must never render as a prompt. These records carry no distinguishing field (isMeta is unset,
# userType is "external" just like a real prompt), so the wrapper text itself is the signal.
_INJECTED_PREFIXES = (
    "<task-notification",
    "<system-reminder",
    "<command-name",
    "<command-message",
    "<local-command-stdout",
    "<local-command-stderr",
    "[SYSTEM NOTIFICATION",
)


def _user_text(record: dict) -> str | None:
    """A genuine user prompt, verbatim — or None if this "user" record isn't one.

    A record with `role: user` is often a tool_result being handed back, not something a human
    typed; those carry no text block, so they fall out here. Meta records and harness-injected
    wrappers (a task notification, a system reminder) are dropped so they never pose as intent.
    """
    if record.get("isMeta"):
        return None
    parts = [
        block.get("text", "")
        for block in _blocks(record)
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    text = "\n".join(part for part in parts if part).strip()
    if not text or text.lstrip().startswith(_INJECTED_PREFIXES):
        return None
    return text


def _assistant(record: dict) -> list:
    """The assistant's prose and actions, in the order they occurred. `thinking` is dropped.

    Returns a mixed list of prose strings and Actions; keeping document order is what lets the
    card show that a commit came after the tests, not just that both happened.
    """
    items: list = []
    for block in _blocks(record):
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text = block.get("text", "").strip()
            if text:
                items.append(text)
        elif kind == "tool_use":
            action = _action(block)
            if action is not None:
                items.append(action)
        # "thinking" and anything else: dropped.
    return items


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
        return Action("command", cards._oneline(command)) if command else None
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
        time = cards._hhmm(record.get("timestamp"))
        if rtype == "user":
            text = _user_text(record)
            if text:
                turns.append(Turn("user", time, [text]))
        elif rtype == "assistant":
            items = _assistant(record)
            if not items:
                continue
            if turns and turns[-1].role == "assistant":
                turns[-1].items.extend(items)
            else:
                turns.append(Turn("assistant", time, items))
    return turns


def parse(session: Path, *, crate_version: str) -> Card | None:
    """Parse a Claude Code session JSONL into a Card, or None if there's nothing usable in it."""
    records = cards._load_records(session)
    live = _live_path(records)
    if not live:
        return None

    turns = _turns(live)
    timestamps = [r["timestamp"] for r in live if r.get("timestamp")]

    return Card(
        source=SOURCE,
        session_id=cards._last(live, "sessionId"),
        turns=turns,
        started=cards._local(min(timestamps)) if timestamps else "",
        ended=cards._local(max(timestamps)) if timestamps else "",
        cwd=cards._last(live, "cwd"),
        git_branch=cards._last(live, "gitBranch"),
        tool_version=cards._last(live, "version"),
        crate_version=crate_version,
        cursor=live[-1].get("uuid", ""),
        records=len(records),
    )
