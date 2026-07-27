"""Turn a Codex CLI session JSONL into a session card — the Codex source adapter.

The mirror of `claude.py`, and the proof that the card model generalizes (issue #8): same
`Card`, same renderer, same cursor — a different on-disk format in front. Where Claude Code
writes a *tree* (`parentUuid`, rewinds, sidechains) that has to be walked to its live leaf,
Codex writes a **flat append-only log** at `~/.codex/sessions/<Y>/<M>/<D>/rollout-*.jsonl`:
one `{timestamp, type, payload}` object per line, in the order things happened. So there is no
tree to walk — reconstruction is a linear scan of the `response_item` records, keeping the same
things the Claude adapter keeps (intent, prose, files touched, commands) and discarding the
same noise (reasoning, tool-result bodies, harness-injected context).

See docs/adr/0014-shared-card-core-per-source-adapters.md and docs/architecture.md.
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
# The "agent history" prefix is the important one: on resume, and after each approval, Codex
# replays the prior transcript back to the model as a *user*-role message (a resumed session is
# a new rollout file, so it has to re-seed context). These are plain prose, not tagged, and can
# be tens of kilobytes each — left in, they render as giant fake prompts and duplicate content
# already captured in the parent segment's card.
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
    """The first `session_meta` payload — cwd, git, versions — or an empty dict if absent."""
    for record in records:
        if record.get("type") == "session_meta":
            payload = record.get("payload")
            return payload if isinstance(payload, dict) else {}
    return {}


def _rollout_id(meta: dict) -> str:
    """The id that identifies *this rollout file* — the card's identity.

    Codex resumes a session into a *new* file rather than appending, and in that new file
    `session_meta.id` is the per-file id (it matches the filename and is unique) while
    `session_meta.session_id` is the shared thread root the resume forked from (its
    `parent_thread_id`). Keying the card on `session_id` would collapse every resume segment of
    a thread onto one filename, so the last capture silently overwrites the rest. So the card is
    keyed on `id` — one rollout file, one card, the way the Claude adapter treats one file. Older
    rollouts carry only `id`; the oldest could in theory carry only `session_id`, so fall back.
    """
    return str(meta.get("id") or meta.get("session_id") or "")


def parse(session: Path, *, crate_version: str) -> Card | None:
    """Parse a Codex session JSONL into a Card, or None if there's nothing usable in it."""
    records = cards._load_records(session)
    meta = _meta(records)

    # Subagent threads (Codex's `guardian` auto-approver, and any future multi-agent worker) are
    # stored as their own rollout files, linked to the primary session by `parent_thread_id`.
    # They are Codex's equivalent of Claude Code's sidechains, which the card model drops (ADR
    # docs/architecture.md, the Keep/Drop/Collapse table): they carry no user intent, only
    # machine chatter (approval decisions like `{"outcome":"allow"}`) and the parent transcript
    # replayed back for the worker to read. Capturing one yields a card of pure noise, so skip it.
    if meta.get("thread_source") == "subagent":
        return None

    turns = _turns(records)
    if not turns:
        return None

    git = meta.get("git")
    git_branch = str(git.get("branch") or "") if isinstance(git, dict) else ""
    timestamps = [r["timestamp"] for r in records if r.get("timestamp")]

    return Card(
        source=SOURCE,
        session_id=_rollout_id(meta),
        turns=turns,
        started=cards._local(min(timestamps)) if timestamps else "",
        ended=cards._local(max(timestamps)) if timestamps else "",
        cwd=str(meta.get("cwd") or ""),
        git_branch=git_branch,
        tool_version=str(meta.get("cli_version") or ""),
        crate_version=crate_version,
        cursor=str(len(records)),
        records=len(records),
    )


# --------------------------------------------------------------------------------------
# the sweep — manual, since Codex has no Stop-hook equivalent to drive capture per session
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ScanSummary:
    """What one `capture_all` sweep did."""

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


def capture_all(
    vault_path: Path, *, crate_version: str, sessions_dir: Path | None = None
) -> ScanSummary:
    """Sweep every Codex rollout under `sessions_dir` into `vault_path`, idempotently.

    The vault is validated once up front (raises VaultError, same as `cards.capture` — this is
    the strict path a human runs, not the hook's fail-quiet one). Each rollout is then parsed and
    written independently: a `None` parse (a subagent/guardian thread, or a file with nothing
    usable) is skipped, not an error; any other exception from parsing or writing one rollout is
    also caught and counted as skipped, so one bad file among many can't abort the rest of the
    sweep.
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
            card = parse(rollout, crate_version=crate_version)
            if card is None:
                skipped += 1
                continue
            result = cards.write(card, vault_path)
        except Exception:  # noqa: BLE001 — one bad rollout must not abort the sweep
            skipped += 1
            continue

        if result.written:
            captured.append(result.card_path)
        else:
            unchanged += 1

    return ScanSummary(len(rollouts), captured, unchanged, skipped)
