"""Turn a Codex CLI session JSONL into a session card — the Codex source adapter.

The mirror of `claude.py`, and the proof that the card model generalizes (issue #8): same
`Card`, same renderer, same cursor — a different on-disk format in front. Where Claude Code
writes a *tree* (`parentUuid`, rewinds, sidechains) that has to be walked to its live leaf,
Codex writes a **flat append-only log** at `~/.codex/sessions/<Y>/<M>/<D>/rollout-*.jsonl`:
one `{timestamp, type, payload}` object per line, in the order things happened. So there is no
tree to walk — reconstruction is a linear scan of the `response_item` records, keeping the same
things the Claude adapter keeps (intent, prose, files touched, commands) and discarding the
same noise (reasoning, tool-result bodies, harness-injected context).

One difference does survive into the card model: a Codex resume appends to the *same* rollout, so
one file is one thread that can run for weeks — and a thread is split into one card per local day
it was active on rather than one card for all of it. See
docs/adr/0014-shared-card-core-per-source-adapters.md,
docs/adr/0015-a-day-of-a-thread-is-a-card.md, and docs/architecture.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from crate_wiki import cards, vault
from crate_wiki.cards import Action, Card, Turn

SOURCE = "codex"

# Where Codex CLI writes rollouts. Overridable (`capture_all`'s `sessions_dir`) so tests don't
# have to touch the real one.
DEFAULT_SESSIONS_DIR = Path.home() / ".codex" / "sessions"


# --------------------------------------------------------------------------------------
# extracting what to keep
# --------------------------------------------------------------------------------------


# Wrappers the harness injects into user- and developer-role messages — environment context,
# plugin lists, skill scaffolding, permission and collaboration-mode blocks. None was typed by
# a human, so it must never render as a prompt. `developer` messages are dropped wholesale (they
# are always injected); for `user` messages the leading tag or header is the only signal, the
# same way it is for Claude Code.
#
# The "agent history" prefix is the important one: after each approval, and again on resume,
# Codex replays the transcript so far back to the model as a *user*-role message. These are plain
# prose, not tagged, and can be tens of kilobytes each — left in, they render as giant fake
# prompts and repeat content this same card already holds from earlier in the file.
_INJECTED_PREFIXES = (
    "<environment_context",
    "<recommended_plugins",
    "<turn_aborted",
    "<skill",
    "<permissions",
    "<collaboration_mode",
    "<app-context",
    "<proposed_plan",
    "# AGENTS",
    "# Files mentioned by the user",
    "## Additional Context",
    "## Code review guidelines",
    "The following is the Codex agent history",
)

# A file operation inside an `apply_patch` envelope. The target path is not a JSON field — it
# sits after the op marker in the patch body, so a line like `*** Update File: src/foo.py` is
# the only place the edited path appears.
_PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.MULTILINE)


def _content_text(payload: dict) -> str:
    """The joined text of a `message` payload's content blocks (input_text / output_text)."""
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    parts = [
        block.get("text", "") for block in content if isinstance(block, dict) and block.get("text")
    ]
    return "\n".join(part for part in parts if part).strip()


def _user_text(payload: dict) -> str | None:
    """A genuine user prompt, verbatim — or None if it's a harness-injected block."""
    text = _content_text(payload)
    if not text or text.lstrip().startswith(_INJECTED_PREFIXES):
        return None
    return text


def _json_args(raw) -> dict:
    """A `function_call`'s `arguments` as a dict. It's a JSON-encoded *string*; a value that
    isn't parseable JSON (a model emitting malformed args) degrades to empty, never raises."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _command_str(cmd) -> str:
    """An `exec_command` command as one string, whether it arrived as a string or an argv list."""
    if isinstance(cmd, list):
        return " ".join(str(part) for part in cmd)
    if isinstance(cmd, str):
        return cmd
    return ""


def _function_action(payload: dict) -> Action | None:
    """A `function_call` as an Action, or None when the tool is noise (a plan update, an
    input request, a stdin write). Only `exec_command` — running a command — survives."""
    if payload.get("name") != "exec_command":
        return None
    command = _command_str(_json_args(payload.get("arguments")).get("cmd"))
    return Action("command", cards._oneline(command)) if command.strip() else None


def _patch_actions(payload: dict) -> list[Action]:
    """An `apply_patch` custom tool call as one edit Action per file it touches.

    A single patch can update, add, and delete several files at once; each `*** … File:` marker
    is one edit. Anything other than `apply_patch` is not a file op and yields nothing.
    """
    if payload.get("name") != "apply_patch":
        return []
    patch = payload.get("input")
    if not isinstance(patch, str):
        return []
    actions: list[Action] = []
    seen: set[str] = set()
    for match in _PATCH_FILE.finditer(patch):
        path = match.group(1).strip()
        if path and path not in seen:
            seen.add(path)
            actions.append(Action("edit", path))
    return actions


def _append_assistant(turns: list[Turn], time: str, items: list) -> None:
    """Add assistant prose/actions, folding onto the current assistant turn if there is one.

    Codex emits each assistant message and each tool call as its own record, so an unfolded
    read would make every step its own turn. Folding consecutive assistant records — until a
    user message breaks the run — keeps the card reading like a conversation, the way Claude's
    naturally-grouped assistant records already do.
    """
    if turns and turns[-1].role == "assistant":
        turns[-1].items.extend(items)
    else:
        turns.append(Turn("assistant", time, items))


def _turns(records: list[dict]) -> list[Turn]:
    """The conversation as turns, read straight down the append-only log (no tree to walk).

    Only `response_item` records are conversation; `session_meta`, `turn_context`, `world_state`
    and the parallel `event_msg` telemetry stream are bookkeeping. Within a `response_item`:
    user/assistant messages become prose, `exec_command`/`apply_patch` become Actions, and
    `developer` messages, `reasoning`, and tool-result outputs are dropped as noise.
    """
    turns: list[Turn] = []
    for record in records:
        if record.get("type") != "response_item":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        ptype = payload.get("type")
        time = cards._hhmm(record.get("timestamp"))

        if ptype == "message":
            role = payload.get("role")
            if role == "user":
                text = _user_text(payload)
                if text:
                    turns.append(Turn("user", time, [text]))
            elif role == "assistant":
                text = _content_text(payload)
                if text:
                    _append_assistant(turns, time, [text])
            # "developer" (always injected) and any other role: dropped.
        elif ptype == "function_call":
            action = _function_action(payload)
            if action is not None:
                _append_assistant(turns, time, [action])
        elif ptype == "custom_tool_call":
            actions = _patch_actions(payload)
            if actions:
                _append_assistant(turns, time, actions)
        # "reasoning", "function_call_output", "custom_tool_call_output", others: dropped.
    return turns


def _meta(records: list[dict]) -> dict:
    """The first `session_meta` payload — cwd, git, versions — or an empty dict if absent.

    A resumed file holds one of these per resume, all carrying the same ids but each carrying the
    cwd, branch and `cli_version` current *at that resume*. This is the file's *header*, and only
    the parts of it that can't drift are read from here: the thread's identity (`_rollout_id`)
    and whether the whole file is a subagent thread. Everything that does drift is read per day
    by `_by_day`, which tracks the meta in force at each day's end.
    """
    for record in records:
        if record.get("type") == "session_meta":
            payload = record.get("payload")
            return payload if isinstance(payload, dict) else {}
    return {}


def _rollout_id(meta: dict) -> str:
    """The id that identifies *this rollout file* — the thread's identity, shared by its cards.

    A Codex resume **appends to the same rollout file**, re-emitting an identical `session_meta`
    rather than starting a new file — checked against a real corpus rather than assumed, and a
    thread can be resumed into one file many times over (issue #32). So one rollout file is one
    thread, and `id` and `session_id` are two names for the same value on every user segment;
    `id` is preferred only because the oldest rollouts predate `session_id` being populated and
    leave it null, never the other way round. A thread is *not* one card — it is one card per day
    it was active on (ADR-0015) — so every card the file yields carries this same id, and the
    cursor keys on the id and the day together.

    `parent_thread_id` is *not* a resume link and must not be read as one. It is set only on
    subagent rollouts (the `guardian` auto-approver), pointing at the session that spawned them;
    no user segment carries it. The field name invites the opposite reading, and the resume model
    above is what makes it moot — see `parse`'s subagent skip.
    """
    return str(meta.get("id") or meta.get("session_id") or "")


# --------------------------------------------------------------------------------------
# splitting a thread into its days — one card per local day the thread was active on
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _DaySlice:
    """One local day of a rollout: its records, and the `session_meta` in force at its end."""

    meta: dict
    records: list[dict]


def _day_key(timestamp, previous: str) -> str:
    """The local calendar day a record belongs to, or `previous` when it can't be read.

    Local wall clock, not UTC — the day boundary is the one the person who did the work lived
    (ADR-0013), and `cards._local` is the same chokepoint `started`/`ended` and every turn stamp
    already go through. A record whose timestamp is missing or unparseable inherits the day of
    the record before it: the log is append-only and in order, so its neighbour is the best
    available answer, and it keeps a file of unreadable timestamps as one card rather than
    fragmenting it. Fail-quiet (ADR-0002) — nothing here raises on a bad value.
    """
    if not isinstance(timestamp, str) or not timestamp:
        return previous
    local = cards._local(timestamp)
    return local[:10] if cards._parse_ts(local) is not None else previous


def _by_day(records: list[dict]) -> list[_DaySlice]:
    """A rollout's records grouped into the local days they happened on, oldest day first.

    A Codex resume appends to the same file (see `_rollout_id`), so one rollout is one thread
    that can span days — and a thread's account of Tuesday belongs on Tuesday, not on the day it
    happened to start (ADR-0015). Grouping is by day *key* rather than by contiguous run, so a
    clock-skewed record still lands in the day it names rather than splitting its day in two.
    Order within a day stays file order, which is the order things happened. Records whose day
    can't be read at all group under `""`, which sorts first and yields one card the way an
    unreadable file did before the split.

    Each day carries the last `session_meta` at or before that day's final record — cwd, branch
    and `cli_version` as they were *that day*, since a resume re-emits a current one and the
    branch in particular drifts across a long thread. Bounding the lookup at the day's own end
    (rather than taking the file's last meta) is what keeps an earlier day's card byte-identical
    when a later day is appended.
    """
    grouped: dict[str, list[dict]] = {}
    metas: dict[str, dict] = {}
    day = ""
    meta: dict = {}

    for record in records:
        if record.get("type") == "session_meta":
            payload = record.get("payload")
            if isinstance(payload, dict):
                meta = payload
        day = _day_key(record.get("timestamp"), day)
        grouped.setdefault(day, []).append(record)
        metas[day] = meta

    return [_DaySlice(metas[key], group) for key, group in sorted(grouped.items())]


def _day_card(slice_: _DaySlice, turns: list[Turn], *, session_id: str, crate_version: str) -> Card:
    """One day of a thread as a Card. Every field is a function of that day's records alone."""
    git = slice_.meta.get("git")
    git_branch = str(git.get("branch") or "") if isinstance(git, dict) else ""
    timestamps = [r["timestamp"] for r in slice_.records if r.get("timestamp")]

    return Card(
        source=SOURCE,
        session_id=session_id,
        turns=turns,
        started=cards._local(min(timestamps)) if timestamps else "",
        ended=cards._local(max(timestamps)) if timestamps else "",
        cwd=str(slice_.meta.get("cwd") or ""),
        git_branch=git_branch,
        tool_version=str(slice_.meta.get("cli_version") or ""),
        crate_version=crate_version,
        # The day's own record count, not the file's: a card's cursor has to move when *that
        # day* gains records and stay put when another day does, or appending Wednesday would
        # rewrite Monday's card on every sweep.
        cursor=str(len(slice_.records)),
        records=len(slice_.records),
    )


def parse(session: Path, *, crate_version: str) -> list[Card]:
    """Parse a Codex rollout into one card per local day it was active on, oldest day first.

    A resume appends to the same rollout file, so one file is one thread that can run for weeks
    (`_rollout_id`). One card for all of it would be dated to the day the thread started and
    invisible to `crate day` on every later day it touched — so the thread is split by day
    instead (ADR-0015). Only days that carry actual conversation get a card: the test is the same
    one a whole file already had to pass — the day's records must yield at least one turn — so a
    dormant stretch between resumes, and a resume whose only content is replayed history, produce
    nothing rather than an empty card. A file with nothing usable anywhere yields `[]`.
    """
    records = cards._load_records(session)
    header = _meta(records)

    # Subagent threads (Codex's `guardian` auto-approver, and any future multi-agent worker) are
    # stored as their own rollout files, linked to the session that spawned them by
    # `parent_thread_id` — the only rollouts that carry that field at all (see `_rollout_id`).
    # They are Codex's equivalent of Claude Code's sidechains, which the card model drops (ADR
    # docs/architecture.md, the Keep/Drop/Collapse table): they carry no user intent, only
    # machine chatter (approval decisions like `{"outcome":"allow"}`) and the parent transcript
    # replayed back for the worker to read. Capturing one yields a card of pure noise, so skip it.
    if header.get("thread_source") == "subagent":
        return []

    session_id = _rollout_id(header)
    parsed: list[Card] = []
    for slice_ in _by_day(records):
        # Split first, then parse: turns are built per day, so `_append_assistant` can never fold
        # a Wednesday record onto a Tuesday turn and Tuesday's card never depends on what came
        # after it.
        turns = _turns(slice_.records)
        if turns:
            parsed.append(
                _day_card(slice_, turns, session_id=session_id, crate_version=crate_version)
            )
    return parsed


# --------------------------------------------------------------------------------------
# the sweep — manual, since Codex has no Stop-hook equivalent to drive capture per session
# --------------------------------------------------------------------------------------


# A top-level `state.json` key, sibling to the per-session `"codex"` cursor `cards.write` keeps.
# That one only gets an entry when a rollout actually yields a card, so a skipped rollout (a
# subagent thread, one with nothing usable) never appears there — using it to answer "is anything
# newer" would make every skipped rollout look permanently unswept. This tracks a different thing:
# the newest rollout *path* a sweep looked at, regardless of what it did with it.
_SWEPT_THROUGH = "codex_swept_through"


@dataclass(frozen=True)
class ScanSummary:
    """What one `capture_all` sweep did.

    `scanned` and `skipped` count *rollout files*; `captured` and `unchanged` count *cards*,
    which is no longer the same number — a thread active on three days is one rollout and three
    cards (ADR-0015).
    """

    scanned: int
    captured: list[Path]
    unchanged: int
    skipped: int


def discover(sessions_dir: Path) -> list[Path]:
    """Every Codex rollout under `sessions_dir`, oldest first, or `[]` if it doesn't exist.

    Sorted by path, not mtime: a rollout's real path is `<Y>/<M>/<D>/rollout-<ISO-ish
    timestamp>-<id>.jsonl`, and both the zero-padded directory levels and the timestamp-prefixed
    filename sort lexicographically in chronological order — so a plain path sort gives "oldest
    first" without ever calling `stat()`, which a synced or restored sessions dir can't be
    trusted to have kept meaningful.
    """
    if not sessions_dir.is_dir():
        return []
    return sorted(sessions_dir.rglob("rollout-*.jsonl"))


def _record_sweep(vault_path: Path, rollouts: list[Path]) -> None:
    """Remember the newest rollout path this sweep looked at, so `count_unswept` can tell what's
    new without opening a file. `rollouts` is already sorted oldest-first, so the last entry is
    the newest. An empty sweep leaves the prior cursor untouched — nothing newer to record."""
    if not rollouts:
        return
    state_path = vault_path / ".crate" / "state.json"
    state = cards._load_state(state_path)
    state[_SWEPT_THROUGH] = str(rollouts[-1])
    cards._write_state(state_path, state)


def count_unswept(vault_path: Path, sessions_dir: Path | None = None) -> int:
    """How many rollouts under `sessions_dir` are newer than the last `capture_all` sweep saw.

    Path comparison against the cursor `_record_sweep` writes to `.crate/state.json` — never opens
    a rollout, so this is cheap enough for `crate pending` to call on every invocation (ADR-0002,
    ADR-0004). A vault that has never run a Codex sweep has no cursor, so everything discovered
    counts as unswept. Read-only and best-effort: an invalid vault just yields no cursor rather
    than raising, since this is a nudge, not an operation with something to protect.
    """
    sessions_dir = (sessions_dir or DEFAULT_SESSIONS_DIR).expanduser().resolve()
    rollouts = discover(sessions_dir)
    if not rollouts:
        return 0
    state = cards._load_state(vault_path / ".crate" / "state.json")
    cursor = state.get(_SWEPT_THROUGH, "")
    return sum(1 for rollout in rollouts if str(rollout) > cursor)


def capture_all(
    vault_path: Path, *, crate_version: str, sessions_dir: Path | None = None
) -> ScanSummary:
    """Sweep every Codex rollout under `sessions_dir` into `vault_path`, idempotently.

    The vault is validated once up front (raises VaultError, same as `cards.capture` — this is
    the strict path a human runs, not the hook's fail-quiet one). Each rollout is then parsed and
    written independently: an empty parse (a subagent/guardian thread, or a file with nothing
    usable) is skipped, not an error; any other exception from parsing or writing one rollout is
    also caught and counted as skipped, so one bad file among many can't abort the rest of the
    sweep. A rollout yields as many cards as it has active days, and each is written on its own
    cursor — so a thread that gained a new day contributes one new card and leaves its earlier
    days untouched.
    """
    vault_path = vault_path.expanduser().resolve()
    vault.load_config(vault_path)  # raises VaultError when it isn't a vault
    sessions_dir = (sessions_dir or DEFAULT_SESSIONS_DIR).expanduser().resolve()

    rollouts = discover(sessions_dir)
    captured: list[Path] = []
    unchanged = 0
    skipped = 0

    for rollout in rollouts:
        try:
            parsed = parse(rollout, crate_version=crate_version)
            if not parsed:
                skipped += 1
                continue
            results = [cards.write(card, vault_path) for card in parsed]
        except Exception:  # noqa: BLE001 — one bad rollout must not abort the sweep
            skipped += 1
            continue

        for result in results:
            if result.written:
                captured.append(result.card_path)
            else:
                unchanged += 1

    _record_sweep(vault_path, rollouts)
    return ScanSummary(len(rollouts), captured, unchanged, skipped)
