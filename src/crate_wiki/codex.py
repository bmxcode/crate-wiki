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
from pathlib import Path

from crate_wiki import cards
from crate_wiki.cards import Action, Card, Turn

SOURCE = "codex"


# --------------------------------------------------------------------------------------
# extracting what to keep
# --------------------------------------------------------------------------------------


# Wrappers the harness injects into user- and developer-role messages — environment context,
# plugin lists, skill scaffolding, permission and collaboration-mode blocks. None was typed by
# a human, so it must never render as a prompt. `developer` messages are dropped wholesale (they
# are always injected); for `user` messages the leading tag or header is the only signal, the
# same way it is for Claude Code.
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
    turns = _turns(records)
    if not turns:
        return None

    meta = _meta(records)
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
