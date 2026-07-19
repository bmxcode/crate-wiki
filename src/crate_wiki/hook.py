"""The Claude Code Stop-hook front-end, and the installer that wires it in.

The hook triggers; the engine decides (ADR-0005). `session.capture()` is strict — it raises
`VaultError` on anything the user must fix — but a Stop hook must never break session exit
(ADR-0002). So `capture_from_hook` swallows *everything*, writes one log line per outcome, and
always returns cleanly; the CLI command around it always exits 0. The strict, raising path
still exists for humans and tests: it's `session.capture()` itself.

Two shapes of "where does the session come from":
- The hook feeds Stop-event JSON on stdin, from which we read `transcript_path`.
- A human or a test passes `--transcript FILE` to bypass stdin. Same fail-quiet behaviour.

`install()` is the other half — it's the interactive command (not the hook), so it *is* strict.
It merges a Stop hook into the user's `~/.claude/settings.json` without clobbering anything
already there. See docs/adr/0002-free-capture-paid-synthesis.md and the pre-push precedent in
vault.py (`_init_git`), which this deliberately mirrors.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
from datetime import datetime
from pathlib import Path

from crate_wiki import session, vault

# Outside any vault, in a directory Claude Code guarantees exists: a broken or missing vault is
# one of the failure modes we must log, so the log cannot live inside the vault it's reporting on.
LOG_PATH = Path.home() / ".claude" / "crate-capture.log"

# The user-global settings file. Capture should fire on every session regardless of repo, so the
# hook belongs here rather than in a project-local .claude/settings.json.
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# The signal `install()` matches on to stay idempotent: any Stop command containing this string
# is "our" hook, to be updated in place rather than duplicated.
STOP_MATCH = "crate capture claude"


# --------------------------------------------------------------------------------------
# the hook front-end — fail-quiet by contract
# --------------------------------------------------------------------------------------


def capture_from_hook(
    *,
    vault_path: Path | None,
    transcript: Path | None,
    stdin_text: str,
    crate_version: str,
    log_path: Path | None = None,
) -> None:
    """Capture the session named on stdin (or by `--transcript`), and never raise.

    Every failure mode — no vault, malformed stdin, a missing transcript, a broken vault, a
    half-written session file — is logged and swallowed. The caller always exits 0. This is the
    ADR-0002 contract: a missed capture is a lost line in a log; a raised capture is a session
    that wouldn't end.
    """
    log_path = log_path or LOG_PATH
    try:
        source = transcript if transcript is not None else _transcript_from_stdin(stdin_text)
        if source is None:
            _log("skip: no transcript on stdin or --transcript", log_path=log_path)
            return
        if vault_path is None:
            _log("skip: no --vault given", log_path=log_path)
            return
        if not source.is_file():
            _log(f"skip: no such transcript {source}", log_path=log_path)
            return

        result = session.capture(source, vault_path, crate_version=crate_version)
        rel = _display_path(result.card_path, vault_path)
        if result.written:
            _log(f"captured {result.session_id[:8]} -> {rel}", log_path=log_path)
        else:
            _log(f"{result.session_id[:8]} already captured, nothing new", log_path=log_path)
    except Exception as error:  # noqa: BLE001 — the whole point is to catch everything
        _log(f"error: {type(error).__name__}: {error}", log_path=log_path)


def _transcript_from_stdin(stdin_text: str) -> Path | None:
    """The `transcript_path` from a Stop-event JSON payload, or None if it isn't there.

    Garbage on stdin (empty, not JSON, JSON without the field) yields None rather than raising —
    a misfiring hook must degrade to "nothing captured", not to a traceback on session exit.
    """
    text = stdin_text.strip()
    if not text:
        return None
    payload = json.loads(text)  # a JSONDecodeError is caught by capture_from_hook
    if not isinstance(payload, dict):
        return None
    value = payload.get("transcript_path")
    return Path(value).expanduser() if isinstance(value, str) and value else None


def _display_path(card_path: Path, vault_path: Path) -> str:
    """The card path relative to the vault root when possible, for a compact log line."""
    try:
        return str(card_path.relative_to(vault_path.expanduser().resolve()))
    except ValueError:
        return str(card_path)


def _log(message: str, *, log_path: Path) -> None:
    """Append one timestamped line. Logging itself must never raise (an unwritable ~/.claude,
    say) — that would defeat the purpose of a fail-quiet hook."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except Exception:  # noqa: BLE001 — even the logger stays silent on failure
        pass


# --------------------------------------------------------------------------------------
# the installer — strict; this is the interactive command, not the hook
# --------------------------------------------------------------------------------------


def install(
    vault_path: Path,
    *,
    settings_path: Path | None = None,
    crate_bin: str | None = None,
) -> str:
    """Merge a Stop hook that captures into `vault_path` into `settings_path`. Returns a status.

    Idempotent and non-destructive: an existing `crate capture claude` hook is updated in place,
    any other Stop hooks are preserved, and every other key in settings.json is left untouched.
    A settings.json we can't parse is refused rather than overwritten — the user is told to add
    the snippet by hand. Raises `VaultError` on a bad vault or unreadable settings file.
    """
    settings_path = settings_path or SETTINGS_PATH
    vault_path = vault_path.expanduser().resolve()
    vault.load_config(vault_path)  # raises VaultError when it isn't a vault

    command = _hook_command(vault_path, crate_bin)
    settings = _load_settings(settings_path)

    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise vault.VaultError(
            f"{settings_path} has a non-object 'hooks' — refusing to touch it; add the hook by hand"
        )
    stop = hooks.setdefault("Stop", [])
    if not isinstance(stop, list):
        raise vault.VaultError(
            f"{settings_path} has a non-list 'hooks.Stop' — refusing to touch it; add it by hand"
        )

    existing = _find_our_entry(stop)
    if existing is not None:
        if existing.get("command") == command:
            return "already installed"
        existing["command"] = command
        _write_settings(settings_path, settings)
        return f"updated -> {vault_path}"

    stop.append({"hooks": [{"type": "command", "command": command}]})
    _write_settings(settings_path, settings)
    return f"installed -> {vault_path}"


def _hook_command(vault_path: Path, crate_bin: str | None) -> str:
    """The shell command Claude Code will run on Stop.

    The absolute path to the `crate` binary is baked in: a hook runs with a stripped PATH (the
    failure vault.py's pre-push hook has to detect at runtime), so resolving it once at install
    time sidesteps that entirely. Falls back to bare `crate` when it can't be found on PATH now.
    """
    crate = crate_bin or shutil.which("crate") or "crate"
    return f"{shlex.quote(crate)} capture claude --vault {shlex.quote(str(vault_path))}"


def _find_our_entry(stop: list) -> dict | None:
    """The first command entry under Stop that is our hook, or None. Mutating the returned dict
    updates it in place inside `settings`."""
    for group in stop:
        if not isinstance(group, dict):
            continue
        for entry in group.get("hooks", []):
            if (
                isinstance(entry, dict)
                and entry.get("type") == "command"
                and STOP_MATCH in str(entry.get("command", ""))
            ):
                return entry
    return None


def _load_settings(path: Path) -> dict:
    """settings.json as a dict, or an empty one when the file is absent. A malformed file is a
    hard error — we won't overwrite a hand-edited file we can't understand."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise vault.VaultError(
            f"could not read {path} ({error}) — add the Stop hook by hand rather than risk it"
        ) from error
    if not isinstance(data, dict):
        raise vault.VaultError(f"{path} is not a JSON object — add the Stop hook by hand")
    return data


def _write_settings(path: Path, settings: dict) -> None:
    """Write settings.json atomically, so an interrupted write can't corrupt it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".crate-tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
