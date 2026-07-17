"""The `crate` command.

Everything here is deterministic: the CLI does mechanics so the LLM can spend its
tokens on judgment. See docs/adr/0004-deterministic-cli.md.
"""

import typer

from crate_wiki import __version__

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
