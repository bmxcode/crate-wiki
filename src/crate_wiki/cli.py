"""The `crate` command.

Everything here is deterministic: the CLI does mechanics so the LLM can spend its
tokens on judgment. See docs/adr/0004-deterministic-cli.md.
"""

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from crate_wiki import __version__, vault

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
