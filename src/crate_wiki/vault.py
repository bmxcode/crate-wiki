"""Scaffolding a vault, and the deterministic checks a vault's git hooks call back into.

`--scope` picks a preset; `.crate/config.toml` is the truth afterwards. Everything here is
mechanics — see docs/adr/0004-deterministic-cli.md.
"""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from pathlib import Path
from string import Template

CONFIG_VERSION = 1

PAGE_TYPES = ("source", "entity", "concept", "synthesis", "daily")

WIKI_DIRS = ("sources", "entities", "concepts", "syntheses", "daily")

# Subdirectories a section wants on creation. Sections not listed get no children.
SECTION_CHILDREN = {"sessions": ("claude-code", "codex")}


@dataclass(frozen=True)
class Section:
    """A source category under `raw/`.

    `private` means gitignored — and, per ADR-0006, that nothing derived from it may be
    written into `wiki/`. The gitignore is only half of that rule; the schema is the other.
    """

    name: str
    private: bool


@dataclass(frozen=True)
class Preset:
    sections: tuple[Section, ...]
    push_policy: str


_COMMON_SECTIONS = (
    Section("sessions", private=False),
    Section("clips", private=False),
    Section("youtube", private=False),
    Section("pastes", private=False),
    Section("assets", private=False),
)

PRESETS = {
    "personal": Preset(
        sections=(*_COMMON_SECTIONS, Section("journal", private=True)),
        push_policy="any",
    ),
    "work": Preset(
        sections=_COMMON_SECTIONS,
        push_policy="allowlist",
    ),
}


class VaultError(Exception):
    """Something went wrong that the user needs to fix."""


def template_text(*parts: str) -> str:
    """A template shipped inside the package. Public because `wiki` regenerates from these too."""
    return (files("crate_wiki") / "templates").joinpath(*parts).read_text(encoding="utf-8")


def _template_names(*parts: str) -> list[str]:
    """The filenames in a shipped template directory, sorted."""
    directory = (files("crate_wiki") / "templates").joinpath(*parts)
    return sorted(entry.name for entry in directory.iterdir() if entry.is_file())


def _render(text: str, **values: str) -> str:
    return Template(text).safe_substitute(**values)


# --------------------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------------------


def render_config(scope: str, preset: Preset, *, today: str, version: str) -> str:
    lines = [
        f"config_version = {CONFIG_VERSION}",
        f'scope = "{scope}"',
        f'created = "{today}"',
        f'crate_version = "{version}"',
        "",
        "# Sections under raw/. private = true means the section is gitignored and stays on",
        "# this machine — and that nothing derived from it may be written into wiki/. The",
        "# gitignore is only half of that rule; the vault's CLAUDE.md is the other half.",
        "# See crate-wiki ADR-0006.",
    ]
    for section in preset.sections:
        lines += [
            "",
            "[[raw.sections]]",
            f'name = "{section.name}"',
            f"private = {str(section.private).lower()}",
        ]

    lines += ["", "[git]"]
    if preset.push_policy == "allowlist":
        lines += [
            '# "allowlist": pushes are refused unless the remote host matches an entry below.',
            "# The list is empty, so this vault pushes nowhere — that is ADR-0001's position,",
            "# expressed as config rather than code.",
            "#",
            "# Populating this list reopens ADR-0001, which rejected an employer-provided git",
            "# host for reasons that were never technical. Write ADR-0007 before adding a host.",
            'push_policy = "allowlist"',
            "push_allowlist = []",
        ]
    else:
        lines += ['push_policy = "any"']

    return "\n".join(lines) + "\n"


def load_config(vault: Path) -> dict:
    config = vault / ".crate" / "config.toml"
    if not config.is_file():
        raise VaultError(f"not a crate vault (no .crate/config.toml): {vault}")
    with config.open("rb") as handle:
        return tomllib.load(handle)


# --------------------------------------------------------------------------------------
# push policy
# --------------------------------------------------------------------------------------


def remote_host(remote: str) -> str | None:
    """The host in a git remote URL, for `scp`-like and URL forms alike.

    `git@github.com:me/x.git` and `https://github.com/me/x.git` both yield `github.com`.
    Returns None when no host can be read, which callers must treat as "refuse".
    """
    remote = remote.strip()
    if not remote:
        return None

    if "://" in remote:
        rest = remote.split("://", 1)[1]
    elif ":" in remote and "/" not in remote.split(":", 1)[0]:
        # scp-like: [user@]host:path
        rest = remote.split(":", 1)[0]
        return rest.rpartition("@")[2].lower() or None
    else:
        # A local path, or something we don't recognise. No host to check.
        return None

    authority = rest.split("/", 1)[0]
    host = authority.rpartition("@")[2]
    host = host.split(":", 1)[0]  # strip any :port
    return host.lower() or None


def push_is_allowed(config: dict, remote: str) -> tuple[bool, str]:
    """Whether this vault may push to `remote`, and why.

    Fails closed: anything unrecognised is refused rather than waved through.
    """
    git = config.get("git", {})
    policy = git.get("push_policy", "allowlist")

    if policy == "any":
        return True, "push_policy is 'any'"

    if policy != "allowlist":
        return False, f"unknown push_policy {policy!r} — refusing rather than guessing"

    allowlist = [str(host).lower() for host in git.get("push_allowlist", [])]
    if not allowlist:
        return False, "this vault has an empty push_allowlist, so it pushes nowhere (ADR-0001)"

    host = remote_host(remote)
    if host is None:
        return False, f"could not read a host from remote {remote!r} — refusing"

    if host in allowlist:
        return True, f"{host} is allowlisted"
    return False, f"{host} is not in this vault's push_allowlist"


# --------------------------------------------------------------------------------------
# scaffolding
# --------------------------------------------------------------------------------------


def _gitignore(preset: Preset) -> str:
    lines = [
        "# This is a vault, so raw/ IS committed here — it's half the content.",
        "# (The engine repo gitignores raw/ for the opposite reason: it must never hold",
        "# vault content. Same line, opposite intent. See crate-wiki ADR-0003.)",
        "",
        ".DS_Store",
        ".obsidian/workspace.json",
        ".obsidian/workspace-mobile.json",
        "",
        "# A machine-local capture cursor. Syncing it would only produce conflicts.",
        ".crate/state.json",
    ]

    private = [section for section in preset.sections if section.private]
    if private:
        lines += [
            "",
            "# Private sections. The vault's CLAUDE.md carries the other half of this rule:",
            "# nothing derived from these may be written into wiki/. See crate-wiki ADR-0006.",
        ]
        lines += [f"raw/{section.name}/" for section in private]

    return "\n".join(lines) + "\n"


def _schema(scope: str, preset: Preset, vault_name: str) -> str:
    private = [section for section in preset.sections if section.private]
    if private:
        section_list = "\n".join(f"- `raw/{section.name}/`" for section in private)
        private_block = (
            "\n"
            + _render(
                template_text("vault", "private-sections.md.tmpl"), section_list=section_list
            ).strip()
            + "\n"
        )
    else:
        private_block = ""

    scope_block = "\n" + template_text("vault", f"scope-{scope}.md").strip()

    schema = _render(
        template_text("vault", "CLAUDE.md.tmpl"),
        vault_name=vault_name,
        scope=scope,
        private_sections=private_block,
        scope_section=scope_block,
    )
    return schema.rstrip() + "\n"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _keep(directory: Path, *, tracked: bool = True) -> None:
    """Create a directory, with a marker so git carries it while it's still empty.

    A private section gets no marker: git ignores the whole directory, so a `.gitkeep`
    there would be a file that looks like it does a job it cannot do.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if tracked:
        (directory / ".gitkeep").write_text("", encoding="utf-8")


def _init_git(vault: Path, preset: Preset) -> list[str]:
    """`git init`, and a pre-push hook when the policy needs one. Returns any warnings."""
    try:
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=vault,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        return [f"could not run `git init` ({error}) — the vault is fine, but isn't a repo yet"]

    if preset.push_policy != "allowlist":
        return []

    hook = vault / ".git" / "hooks" / "pre-push"
    _write(
        hook,
        # The hook triggers; the engine decides (ADR-0005). Parsing git remote URLs in sh is
        # the kind of thing that looks fine until it doesn't, and it can't be unit tested.
        #
        # A hook runs with git's PATH, not a login shell's, so `crate` can be missing here
        # even when it works in a terminal. That must refuse the push — but silently failing
        # on "exec: not found" tells the user nothing, so say what happened.
        "#!/bin/sh\n"
        "# Installed by `crate init`. See .crate/config.toml [git].\n"
        "\n"
        "if ! command -v crate >/dev/null 2>&1; then\n"
        '    echo "crate: not on PATH, so this vault\'s push policy cannot be checked." >&2\n'
        '    echo "crate: refusing the push rather than assuming it is allowed." >&2\n'
        "    exit 1\n"
        "fi\n"
        "\n"
        'exec crate check-push --vault "$(git rev-parse --show-toplevel)" --remote "$2"\n',
    )
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return []


def create(path: Path, scope: str, *, version: str, today: str | None = None) -> list[str]:
    """Scaffold a vault at `path`. Returns warnings worth showing the user.

    Refuses a non-empty directory: `crate init` is a contract with existing vaults
    (ADR-0003), and clobbering one is not recoverable.
    """
    preset = PRESETS[scope]
    today = today or date.today().isoformat()
    vault = path.expanduser().resolve()

    if vault.exists() and any(vault.iterdir()):
        raise VaultError(f"{vault} already exists and isn't empty — refusing to touch it")

    vault.mkdir(parents=True, exist_ok=True)

    _write(vault / "index.md", template_text("vault", "index.md"))
    _write(vault / "log.md", _render(template_text("vault", "log.md.tmpl"), today=today))
    _write(vault / ".gitignore", _gitignore(preset))

    for section in preset.sections:
        children = SECTION_CHILDREN.get(section.name, ())
        tracked = not section.private
        if children:
            for child in children:
                _keep(vault / "raw" / section.name / child, tracked=tracked)
        else:
            _keep(vault / "raw" / section.name, tracked=tracked)

    for name in WIKI_DIRS:
        _keep(vault / "wiki" / name)

    _write(
        vault / ".crate" / "config.toml", render_config(scope, preset, today=today, version=version)
    )
    _write(vault / ".crate" / "state.json", "{}\n")

    baseline: dict[str, str] = {}
    for destination, text in engine_files(scope, vault.name):
        _write(vault.joinpath(*destination), text)
        baseline["/".join(destination)] = _digest(text)

    # Seeded files are deliberately absent from the baseline: it exists to decide whether
    # overwriting is safe, and these are never overwritten.
    for destination, text in seeded_files(vault.name):
        _write(vault.joinpath(*destination), text)

    write_baseline(vault, baseline, version)

    return _init_git(vault, preset)


# --------------------------------------------------------------------------------------
# upgrading an existing vault
# --------------------------------------------------------------------------------------


def engine_files(scope: str, vault_name: str) -> list[tuple[tuple[str, ...], str]]:
    """Files the engine owns inside a vault, as (destination, the exact text to write).

    `create` writes these and `upgrade` rewrites them, from one list so the two can't drift.
    Content rather than a template name, because `CLAUDE.md` is rendered per vault and the
    alternative is the same special case in both callers — see ADR-0010, which moved Layer 3's
    schema onto this list. Slash commands have to be on disk for Claude Code to find them, which
    is why they're copied rather than read from the package.
    """
    preset = PRESETS[scope]
    files: list[tuple[tuple[str, ...], str]] = [
        (("CLAUDE.md",), _schema(scope, preset, vault_name)),
        (("AGENTS.md",), template_text("vault", "AGENTS.md")),
    ]
    for name in _template_names("pages"):
        files.append(((".crate", "templates", name), template_text("pages", name)))
    for name in _template_names("commands"):
        files.append(((".claude", "commands", name), template_text("commands", name)))
    return files


def seeded_files(vault_name: str) -> list[tuple[tuple[str, ...], str]]:
    """Files the engine creates once and then never writes again.

    `CONVENTIONS.md` is where a vault records what it has decided for itself, so `upgrade`
    installs it when it's missing and leaves it alone forever after. A third class rather than a
    special case: ADR-0009 requires every file the engine ships to be classified when it's
    added, and "installs once, then hands it over" is neither engine-owned nor authored.
    """
    conventions = _render(template_text("vault", "CONVENTIONS.md.tmpl"), vault_name=vault_name)
    return [(("CONVENTIONS.md",), conventions.rstrip() + "\n")]


def _digest(text: str) -> str:
    """A content hash of what the engine wrote.

    Content, not mtime: a `git checkout` rewrites every mtime in a vault, so a timestamp would
    report a fresh clone as edited and nothing else would be wrong with it.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_baseline(vault: Path) -> dict[str, str]:
    """What the engine last wrote into this vault, as `vault-relative path -> digest`.

    Missing or unreadable means "no record" rather than an error. A vault created before the
    baseline existed is the ordinary case, and a corrupt one has to degrade to the same careful
    behaviour — refusing to overwrite — rather than raising somewhere it can't be handled.
    """
    try:
        data = json.loads((vault / ".crate" / "baseline.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return {}
    return {str(name): str(digest) for name, digest in files.items()}


def write_baseline(vault: Path, entries: dict[str, str], version: str) -> None:
    """Record what the engine just wrote, so the next upgrade can tell edited from stale.

    Committed, unlike `.crate/state.json`: this describes the vault's content, which every clone
    shares, rather than this machine's capture cursor, which no two clones do. `crate_version` is
    for whoever opens the file — no logic reads it, because keying behaviour off past releases
    would make the engine a museum of its own versions.
    """
    payload = {"crate_version": version, "files": dict(sorted(entries.items()))}
    _write(vault / ".crate" / "baseline.json", json.dumps(payload, indent=2) + "\n")


@dataclass
class UpgradeReport:
    """What an upgrade did, or would do.

    `edited` and `unclaimed` are both "left alone", kept apart because the reasons differ and so
    do the fixes: one is a file you changed, the other is a file the engine has no record of
    writing and therefore cannot judge.
    """

    created: list[str]
    updated: list[str]
    unchanged: list[str]
    seeded: list[str]
    edited: list[str]
    unclaimed: list[str]
    dry_run: bool


def upgrade(
    path: Path, *, version: str, dry_run: bool = False, adopt: bool = False
) -> UpgradeReport:
    """Refresh the engine-owned files in an existing vault. Returns what changed.

    Engine-owned now includes `CLAUDE.md` and `AGENTS.md` (ADR-0010): the schema holds nothing
    vault-local since `CONVENTIONS.md` exists to hold it, so it can be overwritten like any other
    shipped file. What makes that safe is the baseline — a record of what the engine last wrote,
    which is the only way to tell "you edited this" from "the template moved". Without one, a
    file that differs from what this version ships is left alone rather than guessed at.

    `adopt` says the engine-owned files are unedited whatever they look like: overwrite and
    re-record. That is the one-time migration for a vault created before the baseline existed,
    and the way back for anyone who customised a page template and wants the shipped one.

    Authored files are never written: `index.md`, `log.md`, `wiki/` and `raw/` are yours, and so
    is `CONVENTIONS.md` from the moment it exists.
    """
    vault = path.expanduser().resolve()
    config = load_config(vault)  # raises VaultError when it isn't a vault
    scope = str(config.get("scope", ""))
    if scope not in PRESETS:
        raise VaultError(f"{vault} has an unknown scope {scope!r} — can't tell what it should ship")

    report = UpgradeReport([], [], [], [], [], [], dry_run=dry_run)

    # Seeded first, so a vault about to be told its CLAUDE.md was left alone already has the
    # file those local rules are supposed to move into.
    for destination, text in seeded_files(vault.name):
        target = vault.joinpath(*destination)
        if target.exists():
            continue
        report.seeded.append("/".join(destination))
        if not dry_run:
            _write(target, text)

    baseline = read_baseline(vault)
    recorded: dict[str, str] = {}  # rebuilt, so a file the engine stopped shipping drops out

    for destination, shipped in engine_files(scope, vault.name):
        target = vault.joinpath(*destination)
        relative = "/".join(destination)

        if target.exists():
            current = target.read_text(encoding="utf-8")
            if current == shipped:
                # Also the vault someone edited into agreement with what this version ships.
                report.unchanged.append(relative)
                recorded[relative] = _digest(shipped)
                continue

            was = baseline.get(relative)
            if was is None and not adopt:
                # No record of writing this, so an edit and a copy left by a version that
                # predates the baseline look identical. Refusing is the recoverable half.
                report.unclaimed.append(relative)
                continue
            if was is not None and was != _digest(current) and not adopt:
                report.edited.append(relative)
                recorded[relative] = was  # still the last thing the engine wrote
                continue

            report.updated.append(relative)
        else:
            report.created.append(relative)

        recorded[relative] = _digest(shipped)
        if not dry_run:
            _write(target, shipped)

    if not dry_run:
        write_baseline(vault, recorded, version)
        _bump_version(vault, version)

    return report


def _bump_version(vault: Path, version: str) -> None:
    """Rewrite just the `crate_version` line in config.toml, leaving every other line alone.

    A line edit rather than a re-render: config.toml holds the vault's own settings (the push
    allowlist among them), and regenerating it from the preset would quietly discard them.
    """
    path = vault / ".crate" / "config.toml"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    changed = [
        f'crate_version = "{version}"' if line.startswith("crate_version") else line
        for line in lines
    ]
    path.write_text("\n".join(changed) + "\n", encoding="utf-8")
