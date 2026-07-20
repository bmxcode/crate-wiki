"""Tests for `crate upgrade` — which vault files the engine owns, and which it must not touch.

The boundary under test is ADR-0009: engine-owned files are rewritten, authored files never are.
Fixtures are synthetic and built in tmp_path — no real vault content reaches this repo.
"""

import pytest
from typer.testing import CliRunner

from crate_wiki import __version__, vault
from crate_wiki.cli import app

runner = CliRunner()

AUTHORED = ("CLAUDE.md", "AGENTS.md", "index.md", "log.md")


@pytest.fixture
def made(tmp_path):
    target = tmp_path / "vault"
    result = runner.invoke(app, ["init", str(target), "--scope", "personal"])
    assert result.exit_code == 0, result.output
    return target


def age(target):
    """Make the vault look like one created before this deliverable shipped."""
    (target / ".crate" / "templates" / "concept.md").write_text("stale\n", encoding="utf-8")
    for path in (target / ".claude").rglob("*.md"):
        path.unlink()


# --------------------------------------------------------------------------------------
# what init installs
# --------------------------------------------------------------------------------------


def test_init_installs_the_slash_commands(made):
    assert (made / ".claude" / "commands" / "ingest.md").is_file()


def test_every_engine_file_lands_where_create_and_upgrade_agree_it_should(made):
    for _, destination in vault.engine_files():
        assert made.joinpath(*destination).is_file(), destination


# --------------------------------------------------------------------------------------
# upgrading
# --------------------------------------------------------------------------------------


def test_a_fresh_vault_has_nothing_to_upgrade(made):
    report = vault.upgrade(made, version=__version__)

    assert report.created == []
    assert report.updated == []


def test_a_missing_command_is_added_and_a_stale_template_rewritten(made):
    age(made)
    report = vault.upgrade(made, version=__version__)

    assert ".claude/commands/ingest.md" in report.created
    assert ".crate/templates/concept.md" in report.updated
    assert "stale" not in (made / ".crate" / "templates" / "concept.md").read_text(encoding="utf-8")


def test_upgrading_twice_changes_nothing_the_second_time(made):
    age(made)
    vault.upgrade(made, version=__version__)
    report = vault.upgrade(made, version=__version__)

    assert report.created == []
    assert report.updated == []


def test_a_dry_run_reports_but_writes_nothing(made):
    age(made)
    report = vault.upgrade(made, version=__version__, dry_run=True)

    assert report.created and report.updated
    assert not (made / ".claude" / "commands" / "ingest.md").exists()
    assert (made / ".crate" / "templates" / "concept.md").read_text(encoding="utf-8") == "stale\n"


def test_authored_files_are_never_touched(made):
    """CLAUDE.md, index.md and log.md are yours — an upgrade must not write over your work."""
    for name in AUTHORED:
        path = made / name
        path.write_text(path.read_text(encoding="utf-8") + "\nmy own line\n", encoding="utf-8")
    age(made)

    vault.upgrade(made, version=__version__)

    for name in AUTHORED:
        assert (made / name).read_text(encoding="utf-8").endswith("my own line\n"), name


def test_wiki_pages_survive_an_upgrade(made):
    page = made / "wiki" / "concepts" / "Session Parser.md"
    page.write_text("---\ntype: concept\n---\n\n# Session Parser\n", encoding="utf-8")
    age(made)

    vault.upgrade(made, version=__version__)

    assert page.is_file()


def test_an_edited_schema_is_reported_as_drifted_rather_than_merged(made):
    schema = made / "CLAUDE.md"
    text = schema.read_text(encoding="utf-8")
    schema.write_text(text + "\nmy own house rule\n", encoding="utf-8")

    report = vault.upgrade(made, version=__version__)

    assert report.schema_drifted
    assert "my own house rule" in schema.read_text(encoding="utf-8")


def test_an_untouched_schema_does_not_report_drift(made):
    assert not vault.upgrade(made, version=__version__).schema_drifted


def test_the_config_keeps_its_own_settings_and_only_the_version_moves(tmp_path):
    target = tmp_path / "work"
    runner.invoke(app, ["init", str(target), "--scope", "work"])
    config = target / ".crate" / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "push_allowlist = []", 'push_allowlist = ["git.example.com"]'
        ),
        encoding="utf-8",
    )

    vault.upgrade(target, version="9.9.9")

    loaded = vault.load_config(target)
    assert loaded["crate_version"] == "9.9.9"
    assert loaded["git"]["push_allowlist"] == ["git.example.com"]
    assert loaded["scope"] == "work"


def test_upgrading_something_that_is_not_a_vault_is_refused(tmp_path):
    with pytest.raises(vault.VaultError, match="not a crate vault"):
        vault.upgrade(tmp_path, version=__version__)


# --------------------------------------------------------------------------------------
# the CLI surface
# --------------------------------------------------------------------------------------


def test_the_cli_says_when_there_is_nothing_to_do(made):
    result = runner.invoke(app, ["upgrade", str(made)])

    assert result.exit_code == 0
    assert "Already up to date" in result.output


def test_the_cli_warns_about_a_drifted_schema(made):
    schema = made / "CLAUDE.md"
    schema.write_text(schema.read_text(encoding="utf-8") + "\nmine\n", encoding="utf-8")

    result = runner.invoke(app, ["upgrade", str(made)])

    assert result.exit_code == 0
    assert "CLAUDE.md" in result.output


def test_the_cli_refuses_a_directory_that_is_not_a_vault(tmp_path):
    result = runner.invoke(app, ["upgrade", str(tmp_path)])

    assert result.exit_code == 1
    assert "not a crate vault" in result.output
