"""The `crate` command.

Everything here is deterministic: the CLI does mechanics so the LLM can spend its
tokens on judgment. See docs/adr/0004-deterministic-cli.md.
"""

import sys
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from crate_wiki import __version__, codex, hook, intake, vault, wiki
from crate_wiki import lint as lint_wiki  # the command below is `lint`, so the module needs a name

# Every command below takes the vault it works on. Defaulting to the cwd is the common case by
# far — you run these from inside the vault, or from a slash command whose cwd is the vault.
VaultOption = Annotated[Path, typer.Option("--vault", help="Vault to work on.")]

app = typer.Typer(
    name="crate",
    help="An LLM wiki that compounds what you learn and what you've done.",
    no_args_is_help=True,
    add_completion=False,
)


def _show_version(requested: bool) -> None:
    if requested:
        typer.echo(f"crate {__version__}")
        raise typer.Exit


def _count(n: int, noun: str) -> str:
    """`n` of `noun`, pluralized — for output lines that count two different things at once."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


@app.callback()
def main(
    _version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_show_version,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """crate — keep a wiki that compounds instead of scattering."""


class Scope(StrEnum):
    """Which preset `init` starts from. The vault's config.toml is the truth afterwards."""

    work = "work"
    personal = "personal"


@app.command()
def init(
    path: Annotated[Path, typer.Argument(help="Where to create the vault.")],
    scope: Annotated[Scope, typer.Option("--scope", help="Which preset to start from.")],
) -> None:
    """Scaffold a vault: the schema, the tree, and a git repo to hold them."""
    try:
        warnings = vault.create(path, scope.value, version=__version__)
    except vault.VaultError as error:
        typer.secho(f"crate: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from error

    for warning in warnings:
        typer.secho(f"crate: {warning}", fg=typer.colors.YELLOW, err=True)

    # index.md is generated (ADR-0008). Generating it now rather than shipping a static skeleton
    # means a brand-new vault's index is byte-identical to what `crate index` would produce.
    wiki.reindex(path.expanduser().resolve())

    typer.echo(f"Created a {scope.value} vault at {path}")
    typer.echo("")
    typer.echo("  CLAUDE.md   the schema — read it, then edit it as it earns changes")
    typer.echo("  raw/        Layer 1, immutable")
    typer.echo("  wiki/       Layer 2, LLM-maintained")
    typer.echo("")
    typer.echo(f"Next: open {path} in Obsidian, and read CLAUDE.md.")


capture_app = typer.Typer(
    help="Capture a session into a vault as a card. Free, deterministic, idempotent.",
    no_args_is_help=True,
)
app.add_typer(capture_app, name="capture")


@capture_app.command("claude")
def capture_claude(
    vault_path: Annotated[
        Path | None,
        typer.Option("--vault", help="Vault to write the card into."),
    ] = None,
    transcript: Annotated[
        Path | None,
        typer.Option("--transcript", help="A session JSONL to capture instead of reading stdin."),
    ] = None,
) -> None:
    """Capture the current Claude Code session, driven by its Stop hook.

    The hook feeds Stop-event JSON on stdin; `--transcript FILE` bypasses that for manual runs.
    Either way this is fail-quiet by contract (ADR-0002): it never breaks session exit, always
    exits 0, and writes every outcome — success or failure — to ~/.claude/crate-capture.log.
    """
    stdin_text = ""
    if transcript is None and not sys.stdin.isatty():
        try:
            stdin_text = sys.stdin.read()
        except Exception:  # noqa: BLE001 — a broken stdin must not break session exit either
            stdin_text = ""

    hook.capture_from_hook(
        vault_path=vault_path,
        transcript=transcript,
        stdin_text=stdin_text,
        crate_version=__version__,
    )


@capture_app.command("codex")
def capture_codex(
    vault_path: Annotated[
        Path | None,
        typer.Option("--vault", help="Vault to write cards into."),
    ] = None,
    transcript: Annotated[
        Path | None,
        typer.Option("--transcript", help="A single Codex rollout JSONL to capture."),
    ] = None,
    sessions_dir: Annotated[
        Path | None,
        typer.Option(
            "--sessions-dir",
            help="Where Codex rollouts live. Defaults to ~/.codex/sessions.",
        ),
    ] = None,
) -> None:
    """Capture Codex CLI sessions into a vault as cards.

    Codex has no Stop hook (its `notify` slot fires per turn, not on session exit, and is already
    taken by another program on most machines), so there's no single session to be handed at exit
    the way Claude Code's is. With no `--transcript`, this sweeps every rollout under
    `--sessions-dir` and captures every new or changed one, idempotently — strict like
    `install-hook`: a bad vault is reported and exits 1, it does not fail quietly. `--transcript
    FILE` is the single-file path, same fail-quiet contract as `capture claude`, for a manual
    one-off.
    """
    if transcript is not None:
        hook.capture_from_hook(
            vault_path=vault_path,
            transcript=transcript,
            stdin_text="",
            crate_version=__version__,
            parse=codex.parse,
        )
        return

    if vault_path is None:
        typer.secho("crate: capture codex needs --vault", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    try:
        summary = codex.capture_all(
            vault_path, crate_version=__version__, sessions_dir=sessions_dir
        )
    except vault.VaultError as error:
        raise _fail(error) from error

    # Two units, so the line names them: a thread active on three days is one rollout and three
    # cards (ADR-0015), and "scanned 1, captured 3" reads as a contradiction without the nouns.
    typer.echo(
        f"scanned {_count(summary.scanned, 'rollout')}, skipped {summary.skipped}; "
        f"captured {_count(len(summary.captured), 'card')}, unchanged {summary.unchanged}"
    )
    resolved_vault = vault_path.expanduser().resolve()
    for path in summary.captured:
        try:
            rel = path.relative_to(resolved_vault)
        except ValueError:
            rel = path
        typer.echo(f"  {rel}")


@app.command("install-hook")
def install_hook(
    vault_path: Annotated[Path, typer.Option("--vault", help="Vault captured sessions land in.")],
) -> None:
    """Wire `crate capture claude` into ~/.claude/settings.json as a Stop hook.

    Idempotent and non-destructive: re-running updates our entry in place and leaves any other
    Stop hooks alone. Point it at one vault per machine (that's the isolation model — ADR-0001).
    """
    try:
        status = hook.install(vault_path)
    except vault.VaultError as error:
        typer.secho(f"crate: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from error

    typer.echo(f"Stop hook {status}")
    typer.echo("")
    typer.echo("Every session now captures to the vault on exit — zero tokens, never blocking.")
    typer.echo(f"Outcomes are logged to {hook.LOG_PATH}.")


# --------------------------------------------------------------------------------------
# the mechanical half of the operations — see ADR-0008, and ADR-0012 for `day`
# --------------------------------------------------------------------------------------


def _fail(error: vault.VaultError) -> typer.Exit:
    typer.secho(f"crate: {error}", fg=typer.colors.RED, err=True)
    return typer.Exit(1)


add_app = typer.Typer(
    help="Add non-session material as a raw source: normalize a paste or a web clip. "
    "Offline and deterministic — it normalizes what you have, it never fetches (ADR-0022).",
    no_args_is_help=True,
)
app.add_typer(add_app, name="add")


def _read_content(file: Path | None) -> str:
    """The text to normalize: `--file` if given, else stdin. Unlike capture, this is strict —
    `crate add` is invoked by a person, so an empty input is an error worth reporting, not a
    quiet no-op the way a hook's must be."""
    if file is not None:
        try:
            return file.read_text(encoding="utf-8")
        except OSError as error:
            raise _fail(vault.VaultError(f"can't read {file}: {error}")) from error
    if sys.stdin.isatty():
        raise _fail(vault.VaultError("no input — pass text on stdin or with --file"))
    return sys.stdin.read()


def _write_and_report(vault_path: Path, section: str, captured: str, title: str, content: str):
    try:
        filename = intake.source_filename(captured, title)
        path = intake.write_source(vault_path, section, filename, content)
    except vault.VaultError as error:
        raise _fail(error) from error
    typer.echo(path)


@add_app.command("paste")
def add_paste(
    title: Annotated[str, typer.Option("--title", help="The source title — its H1 and filename.")],
    origin: Annotated[
        str, typer.Option("--from", help="Where it came from: slack, email, teams, …")
    ] = "",
    url: Annotated[
        str, typer.Option("--url", help="A link to the original, if there is one.")
    ] = "",
    file: Annotated[
        Path | None, typer.Option("--file", help="Read the text from here instead of stdin.")
    ] = None,
    when: Annotated[
        str | None, typer.Option("--date", help="Capture date (YYYY-MM-DD). Defaults to today.")
    ] = None,
    vault_path: VaultOption = Path("."),
) -> None:
    """Normalize a pasted message into `raw/pastes/` as a source, its text kept verbatim."""
    captured = when or date.today().isoformat()
    try:
        content = intake.normalize_paste(
            _read_content(file), title=title, origin=origin, url=url, captured=captured
        )
    except vault.VaultError as error:
        raise _fail(error) from error
    _write_and_report(vault_path, "pastes", captured, title, content)


@add_app.command("url")
def add_url(
    url: Annotated[
        str, typer.Option("--url", help="The page URL. Read from a Clipper file if set there.")
    ] = "",
    title: Annotated[
        str, typer.Option("--title", help="Overrides the title read from a Clipper capture.")
    ] = "",
    file: Annotated[
        Path | None,
        typer.Option("--file", help="An Obsidian Clipper .md (or article text) — else stdin."),
    ] = None,
    when: Annotated[
        str | None, typer.Option("--date", help="Capture date (YYYY-MM-DD). Defaults to today.")
    ] = None,
    vault_path: VaultOption = Path("."),
) -> None:
    """Normalize a web clip into `raw/clips/`. A Clipper capture seeds title/url/author."""
    captured = when or date.today().isoformat()
    try:
        content = intake.normalize_clip(
            _read_content(file), url=url, title=title, captured=captured
        )
        # The title may have come from the Clipper frontmatter, so read it back for the filename.
        resolved_title = wiki.read_frontmatter(content).get("title", title)
    except vault.VaultError as error:
        raise _fail(error) from error
    _write_and_report(vault_path, "clips", captured, resolved_title, content)


@app.command()
def upgrade(
    vault_path: Annotated[Path, typer.Argument(help="Vault to upgrade.")] = Path("."),
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would change and write nothing.")
    ] = False,
    adopt: Annotated[
        bool,
        typer.Option(
            "--adopt",
            help="Take ownership of the engine-owned files even if they look edited locally.",
        ),
    ] = False,
) -> None:
    """Refresh the engine-owned files in an existing vault: the schema, page templates, commands.

    Never touches what you author — CONVENTIONS.md, index.md, log.md, wiki/, raw/. CLAUDE.md and
    AGENTS.md are the engine's (ADR-0010), so they're refreshed like anything else, except when
    they look locally edited: those are reported and left alone, and `--adopt` overrides that.
    """
    try:
        report = vault.upgrade(vault_path, version=__version__, dry_run=dry_run, adopt=adopt)
    except vault.VaultError as error:
        raise _fail(error) from error

    labels = (
        ("would add", "would update", "would create")
        if report.dry_run
        else ("added", "updated", "created")
    )
    width = max(len(label) for label in labels)
    written = (report.created, report.updated, report.seeded)
    for label, names in zip(labels, written, strict=True):
        for name in names:
            typer.echo(f"  {label.ljust(width)}  {name}")

    if not any(written) and not report.edited and not report.unclaimed:
        typer.echo("Already up to date.")
    elif report.dry_run:
        typer.echo("\nNothing was written (--dry-run).")

    if report.edited:
        _left_alone(
            report.edited,
            "you've edited them since the engine wrote them",
            vault_path,
        )
    if report.unclaimed:
        _left_alone(
            report.unclaimed,
            "the engine has no record of writing them, so it can't tell an edit\n"
            "crate: from a copy left by a version that predates that record",
            vault_path,
        )


def _left_alone(names: list[str], because: str, vault_path: Path) -> None:
    """Report engine-owned files an upgrade declined to overwrite, and how to unblock them.

    Says what to do rather than only what happened: the fix is always the same two steps, and a
    warning that doesn't name them is one you can only act on by reading the source.
    """
    listed = "\n".join(f"crate:   {name}" for name in names)
    typer.secho(
        f"\ncrate: left alone, because {because}:\n"
        f"{listed}\n"
        "crate:\n"
        "crate: anything this vault decided for itself belongs in CONVENTIONS.md, which an\n"
        "crate: upgrade never touches. Move it there, then take the shipped versions with:\n"
        f"crate:   crate upgrade {vault_path} --adopt",
        fg=typer.colors.YELLOW,
        err=True,
    )


@app.command()
def pending(
    vault_path: VaultOption = Path("."),
    show_all: Annotated[
        bool, typer.Option("--all", help="List ingested sources too, not just new ones.")
    ] = False,
    sessions_dir: Annotated[
        Path | None,
        typer.Option(
            "--sessions-dir",
            help="Where Codex rollouts live. Defaults to ~/.codex/sessions.",
        ),
    ] = None,
) -> None:
    """List raw sources the wiki hasn't folded in yet.

    The ledger is the `sources:` frontmatter on wiki/sources/ pages, so this is idempotent by
    construction: ingest a source and it stops being listed. Private sections never appear
    (ADR-0006). Prints nothing and exits 0 when there's nothing to do.

    Also nudges when Codex has rollouts newer than the last `/fetch-codex` sweep (issue #35):
    unlike Claude Code, Codex has no Stop hook to capture it automatically, so this is the check
    that stops one going unswept because nobody remembered to run it.
    """
    try:
        items = wiki.pending(vault_path, include_all=show_all)
    except vault.VaultError as error:
        raise _fail(error) from error

    # A plain path per line in the common case, so the output stays easy to read and to pipe;
    # anything that isn't simply new gets its status appended.
    for item in items:
        typer.echo(item.path if item.status == "new" else f"{item.path}\t{item.status}")

    resolved_vault = vault_path.expanduser().resolve()
    unswept = codex.count_unswept(resolved_vault, sessions_dir=sessions_dir)
    if unswept:
        typer.echo(f"{unswept} Codex rollouts not yet swept — run /fetch-codex")


@app.command()
def lint(vault_path: VaultOption = Path(".")) -> None:
    """Check the wiki for the things that have a single right answer. Prints nothing when clean.

    One finding per line, `path<TAB>check<TAB>detail`, sorted — the same shape as `crate pending`,
    so `/lint` can read it back and spend its tokens on the four questions code can't answer.
    The checks are dead wikilinks, orphan pages, an index that no longer matches the pages on
    disk, and a page citing a raw source that's private (ADR-0006) or missing.

    **It reports and never repairs**, and it **exits 0 whether or not it found anything** — only a
    bad vault exits 1. Findings are the normal state of a working vault: a concept you haven't
    linked yet, an index one command behind. A gate that fires on the normal state is one you
    turn off, which is the failure this check exists to avoid (ADR-0020).

    Staleness isn't here. `crate pending` reports a raw file that has outrun its page, against the
    digest the page recorded, and a second answer to that question would be one without the ledger.
    """
    try:
        findings = lint_wiki.check(vault_path)
    except vault.VaultError as error:
        raise _fail(error) from error

    for finding in findings:
        typer.echo(f"{finding.path}\t{finding.check}\t{finding.detail}")


@app.command()
def day(
    when: Annotated[
        str | None,
        typer.Argument(help="A date (YYYY-MM-DD), 'today', or 'yesterday'. Defaults to yesterday."),
    ] = None,
    vault_path: VaultOption = Path("."),
) -> None:
    """List one day's session cards, oldest first — the raw input `/daily` reads.

    Prints the resolved date, then one card path per line. Order is by when each session
    started, not by filename: a card is `<date>-<short id>.md`, so sorting names sorts by id.

    The date is printed because it's the one thing the caller can't recover from the paths on a
    day that has none — and resolving "yesterday" is a question about today's date, which is
    what a model answers confidently and wrongly (ADR-0012).
    """
    try:
        resolved = wiki.resolve_day(when)
        cards = wiki.day_cards(vault_path, resolved)
    except vault.VaultError as error:
        raise _fail(error) from error

    typer.echo(resolved)
    for card in cards:
        typer.echo(card)


@app.command("new")
def new_page(
    page_type: Annotated[str, typer.Argument(help="source, entity, concept, synthesis or daily.")],
    title: Annotated[str, typer.Argument(help="The page title — also its filename and H1.")],
    vault_path: VaultOption = Path("."),
    raw: Annotated[
        str | None,
        typer.Option("--raw", help="Raw path this page summarises. Required for a source page."),
    ] = None,
) -> None:
    """Scaffold a wiki page from the vault's template, with the frontmatter filled in.

    Refuses to overwrite an existing page — extending one is an edit.
    """
    try:
        path = wiki.new_page(vault_path, page_type, title, raw=raw)
    except vault.VaultError as error:
        raise _fail(error) from error

    typer.echo(path)


@app.command("index")
def reindex(vault_path: VaultOption = Path(".")) -> None:
    """Regenerate index.md from every page's `summary:` frontmatter.

    index.md is derived, not authored (ADR-0008): a page's one-liner lives on the page. Pages are
    grouped by directory rather than by the type they claim, so a page with broken frontmatter is
    still listed — being absent from the index is the one failure it exists to prevent.
    """
    try:
        path = wiki.reindex(vault_path)
    except vault.VaultError as error:
        raise _fail(error) from error

    typer.echo(f"Rewrote {path}")


@app.command("extend")
def extend_page(
    title: Annotated[str, typer.Argument(help="The page to extend, by title.")],
    vault_path: VaultOption = Path("."),
    source: Annotated[
        str | None,
        typer.Option("--source", help="Source this page absorbed, e.g. '[[Session · …]]'."),
    ] = None,
) -> None:
    """Record that an existing page absorbed new material: bump `updated:`, add to `sources:`.

    `created:` is never touched, and a source already listed isn't added twice — so re-running
    this is a no-op. On a source page `sources:` is the ingest ledger, which is why appending to
    it is code rather than a hand edit.
    """
    try:
        path, changed = wiki.extend_page(vault_path, title, source=source)
    except vault.VaultError as error:
        raise _fail(error) from error

    typer.echo(f"{'Updated' if changed else 'Already current'} {path.name}")


@app.command("fmt")
def format_wiki(vault_path: VaultOption = Path(".")) -> None:
    """Put every wiki page's paragraphs back on one line each.

    Obsidian renders a single newline inside a paragraph as a line break, so a hard-wrapped page
    reads as shredded prose in the view the vault exists to be browsed in. Headings, tables, code
    blocks, blockquotes and explicit hard breaks are left exactly as they are.
    """
    try:
        changed = wiki.format_pages(vault_path)
    except vault.VaultError as error:
        raise _fail(error) from error

    for path in changed:
        typer.echo(f"  reflowed  {path.name}")
    if not changed:
        typer.echo("Nothing to reflow.")


@app.command("log")
def log_entry(
    operation: Annotated[str, typer.Argument(help="The operation: ingest, ask, daily, lint.")],
    title: Annotated[str, typer.Option("--title", help="What the entry is about.")],
    vault_path: VaultOption = Path("."),
) -> None:
    """Append one `## [YYYY-MM-DD] op | Title` entry to log.md.

    Append-only: prior lines are never read for content, rewritten or reordered.
    """
    try:
        line = wiki.append_log(vault_path, operation, title)
    except vault.VaultError as error:
        raise _fail(error) from error

    typer.echo(line)


@app.command(hidden=True)
def check_push(
    vault_path: Annotated[Path, typer.Option("--vault", help="Vault root.")],
    remote: Annotated[str, typer.Option("--remote", help="Remote URL git is pushing to.")],
) -> None:
    """Decide whether this vault may push to a remote. Called by the pre-push hook.

    The hook triggers; this decides (ADR-0005). Exits non-zero to refuse the push.
    """
    try:
        config = vault.load_config(vault_path)
    except vault.VaultError as error:
        typer.secho(f"crate: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from error

    allowed, reason = vault.push_is_allowed(config, remote)
    if not allowed:
        typer.secho(f"crate: refusing to push — {reason}.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
