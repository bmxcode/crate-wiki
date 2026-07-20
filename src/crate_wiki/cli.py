"""The `crate` command.

Everything here is deterministic: the CLI does mechanics so the LLM can spend its
tokens on judgment. See docs/adr/0004-deterministic-cli.md.
"""

import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from crate_wiki import __version__, hook, vault

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
