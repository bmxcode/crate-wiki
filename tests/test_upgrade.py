"""Tests for `crate upgrade` — which vault files the engine owns, and which it must not touch.

The boundary under test is ADR-0009 as revised by ADR-0010: `CLAUDE.md` and `AGENTS.md` are the
engine's, `CONVENTIONS.md` is the vault's, and what makes overwriting the schema safe is the
baseline — a record of what the engine last wrote, without which an edit and a stale copy are
indistinguishable. Fixtures are synthetic and built in tmp_path; nothing here reads a clock, since
the baseline hashes content rather than comparing mtimes.
"""

import json

import pytest
from typer.testing import CliRunner

from crate_wiki import __version__, vault
from crate_wiki.cli import app

runner = CliRunner()

AUTHORED = ("CONVENTIONS.md", "index.md", "log.md")

STALE = "stale\n"

HOUSE_RULE = "\nmy own house rule\n"


@pytest.fixture
def made(tmp_path):
    target = tmp_path / "vault"
    result = runner.invoke(app, ["init", str(target), "--scope", "personal"])
    assert result.exit_code == 0, result.output
    return target


def age(target):
    """Make the vault look like one an older engine version created.

    Stale content *and* a baseline that records that stale content — because the older engine
    genuinely did write it. Mutating the file alone would simulate a local edit instead, which
    is the other half of what these tests have to tell apart.
    """
    (target / ".crate" / "templates" / "concept.md").write_text(STALE, encoding="utf-8")
    for path in (target / ".claude").rglob("*.md"):
        path.unlink()

    baseline = vault.read_baseline(target)
    baseline[".crate/templates/concept.md"] = vault._digest(STALE)
    baseline.pop(".claude/commands/ingest.md", None)
    baseline.pop(".claude/commands/ask.md", None)
    baseline.pop(".claude/commands/daily.md", None)
    baseline.pop(".claude/commands/fetch-codex.md", None)
    vault.write_baseline(target, baseline, "0.0.1")


def edit(path, addition=HOUSE_RULE):
    """Append to a vault file, the way someone would who wanted it to say something else."""
    path.write_text(path.read_text(encoding="utf-8") + addition, encoding="utf-8")


def shipped(target, relative):
    """What this engine version would write to `relative` in this vault."""
    scope = vault.load_config(target)["scope"]
    for destination, text in vault.engine_files(scope, target.name):
        if "/".join(destination) == relative:
            return text
    raise AssertionError(f"{relative} is not an engine-owned file")


# --------------------------------------------------------------------------------------
# what init installs
# --------------------------------------------------------------------------------------


def test_init_installs_the_slash_commands(made):
    assert (made / ".claude" / "commands" / "ingest.md").is_file()
    assert (made / ".claude" / "commands" / "ask.md").is_file()


def test_every_engine_file_lands_where_create_and_upgrade_agree_it_should(made):
    for destination, _ in vault.engine_files("personal", made.name):
        assert made.joinpath(*destination).is_file(), destination


def test_init_seeds_a_conventions_file_at_the_vault_root(made):
    conventions = made / "CONVENTIONS.md"

    assert conventions.is_file()
    assert made.name in conventions.read_text(encoding="utf-8")


def test_init_records_a_baseline_for_every_engine_file(made):
    baseline = vault.read_baseline(made)

    for destination, text in vault.engine_files("personal", made.name):
        relative = "/".join(destination)
        assert baseline[relative] == vault._digest(text), relative


def test_the_baseline_does_not_record_the_seeded_file(made):
    """It exists to decide whether overwriting is safe, and CONVENTIONS.md is never overwritten."""
    assert "CONVENTIONS.md" not in vault.read_baseline(made)


# --------------------------------------------------------------------------------------
# upgrading
# --------------------------------------------------------------------------------------


def test_a_fresh_vault_has_nothing_to_upgrade(made):
    report = vault.upgrade(made, version=__version__)

    assert report.created == []
    assert report.updated == []
    assert report.seeded == []
    assert report.edited == []
    assert report.unclaimed == []


def test_a_missing_command_is_added_and_a_stale_template_rewritten(made):
    age(made)
    report = vault.upgrade(made, version=__version__)

    assert ".claude/commands/ingest.md" in report.created
    assert ".crate/templates/concept.md" in report.updated
    assert STALE not in (made / ".crate" / "templates" / "concept.md").read_text(encoding="utf-8")


def test_upgrading_twice_changes_nothing_the_second_time(made):
    age(made)
    vault.upgrade(made, version=__version__)
    report = vault.upgrade(made, version=__version__)

    assert report.created == []
    assert report.updated == []


def test_a_dry_run_reports_but_writes_nothing(made):
    age(made)
    before = (made / ".crate" / "baseline.json").read_text(encoding="utf-8")

    report = vault.upgrade(made, version=__version__, dry_run=True)

    assert report.created and report.updated
    assert not (made / ".claude" / "commands" / "ingest.md").exists()
    assert (made / ".crate" / "templates" / "concept.md").read_text(encoding="utf-8") == STALE
    assert (made / ".crate" / "baseline.json").read_text(encoding="utf-8") == before


def test_a_dry_run_does_not_seed_the_conventions_file(made):
    (made / "CONVENTIONS.md").unlink()

    report = vault.upgrade(made, version=__version__, dry_run=True)

    assert report.seeded == ["CONVENTIONS.md"]
    assert not (made / "CONVENTIONS.md").exists()


def test_authored_files_are_never_touched(made):
    """CONVENTIONS.md, index.md and log.md are yours — an upgrade must not write over your work."""
    for name in AUTHORED:
        path = made / name
        edit(path, "\nmy own line\n")
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


def test_a_vault_with_an_unknown_scope_is_refused_rather_than_guessed_at(made):
    config = made / ".crate" / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace('scope = "personal"', 'scope = "archive"'),
        encoding="utf-8",
    )

    with pytest.raises(vault.VaultError, match="unknown scope"):
        vault.upgrade(made, version=__version__)


# --------------------------------------------------------------------------------------
# the three-way comparison — the baseline is what tells an edit from a stale copy
# --------------------------------------------------------------------------------------


def test_a_stale_but_unedited_schema_is_updated_rather_than_reported(made):
    """The false positive this deliverable exists to kill, pinned.

    A vault whose CLAUDE.md the engine wrote and nobody has touched must take a newer schema
    silently, however far the shipped wording has moved since.
    """
    schema = made / "CLAUDE.md"
    old = schema.read_text(encoding="utf-8").replace("## Page types", "## Page kinds")
    schema.write_text(old, encoding="utf-8")
    baseline = vault.read_baseline(made)
    baseline["CLAUDE.md"] = vault._digest(old)
    vault.write_baseline(made, baseline, "0.0.1")

    report = vault.upgrade(made, version=__version__)

    assert "CLAUDE.md" in report.updated
    assert report.edited == []
    assert report.unclaimed == []
    assert schema.read_text(encoding="utf-8") == shipped(made, "CLAUDE.md")


def test_an_edited_schema_is_left_alone_rather_than_overwritten(made):
    schema = made / "CLAUDE.md"
    edit(schema)

    report = vault.upgrade(made, version=__version__)

    assert report.edited == ["CLAUDE.md"]
    assert "CLAUDE.md" not in report.updated
    assert "my own house rule" in schema.read_text(encoding="utf-8")


def test_an_edited_schema_keeps_its_baseline_entry_so_the_next_run_agrees(made):
    schema = made / "CLAUDE.md"
    was = vault.read_baseline(made)["CLAUDE.md"]
    edit(schema)

    vault.upgrade(made, version=__version__)

    assert vault.read_baseline(made)["CLAUDE.md"] == was
    assert vault.upgrade(made, version=__version__).edited == ["CLAUDE.md"]


def test_adopt_overwrites_an_edited_schema_and_records_it(made):
    schema = made / "CLAUDE.md"
    edit(schema)

    report = vault.upgrade(made, version=__version__, adopt=True)

    assert report.edited == []
    assert "CLAUDE.md" in report.updated
    assert "my own house rule" not in schema.read_text(encoding="utf-8")
    assert vault.read_baseline(made)["CLAUDE.md"] == vault._digest(shipped(made, "CLAUDE.md"))


def test_a_vault_with_no_baseline_adopts_a_schema_that_matches_what_ships(made):
    """crate-personal's case: created before the baseline existed, and provably untouched."""
    (made / ".crate" / "baseline.json").unlink()

    report = vault.upgrade(made, version=__version__)

    assert "CLAUDE.md" in report.unchanged
    assert report.unclaimed == []
    assert vault.read_baseline(made)["CLAUDE.md"] == vault._digest(shipped(made, "CLAUDE.md"))


def test_a_vault_with_no_baseline_leaves_a_differing_schema_alone(made):
    """crate-work's case: stale and unedited, but the engine has no way to know that."""
    schema = made / "CLAUDE.md"
    schema.write_text("an older schema\n", encoding="utf-8")
    (made / ".crate" / "baseline.json").unlink()

    report = vault.upgrade(made, version=__version__)

    assert report.unclaimed == ["CLAUDE.md"]
    assert schema.read_text(encoding="utf-8") == "an older schema\n"
    assert "CLAUDE.md" not in vault.read_baseline(made)


def test_adopt_takes_the_shipped_schema_when_there_is_no_baseline(made):
    schema = made / "CLAUDE.md"
    schema.write_text("an older schema\n", encoding="utf-8")
    (made / ".crate" / "baseline.json").unlink()

    report = vault.upgrade(made, version=__version__, adopt=True)

    assert report.unclaimed == []
    assert schema.read_text(encoding="utf-8") == shipped(made, "CLAUDE.md")


def test_a_customised_page_template_is_kept_rather_than_clobbered(made):
    """ADR-0009 left this open: engine-owned files were overwritten with no way to notice."""
    template = made / ".crate" / "templates" / "concept.md"
    edit(template, "\nmy own field\n")

    report = vault.upgrade(made, version=__version__)

    assert report.edited == [".crate/templates/concept.md"]
    assert "my own field" in template.read_text(encoding="utf-8")


def test_a_file_edited_into_agreement_with_what_ships_is_recorded_not_flagged(made):
    """Someone who pasted in the new schema by hand has an unedited vault, whatever the record."""
    schema = made / "CLAUDE.md"
    baseline = vault.read_baseline(made)
    baseline["CLAUDE.md"] = vault._digest("something else entirely\n")
    vault.write_baseline(made, baseline, "0.0.1")

    report = vault.upgrade(made, version=__version__)

    assert "CLAUDE.md" in report.unchanged
    assert report.edited == []
    assert vault.read_baseline(made)["CLAUDE.md"] == vault._digest(
        schema.read_text(encoding="utf-8")
    )


def test_a_corrupt_baseline_is_read_as_no_record_rather_than_raising(made):
    (made / ".crate" / "baseline.json").write_text("{not json", encoding="utf-8")

    assert vault.read_baseline(made) == {}
    assert vault.upgrade(made, version=__version__).unclaimed == []


def test_the_baseline_drops_a_file_the_engine_no_longer_ships(made):
    baseline = vault.read_baseline(made)
    baseline[".claude/commands/retired.md"] = vault._digest("gone\n")
    vault.write_baseline(made, baseline, "0.0.1")

    vault.upgrade(made, version=__version__)

    assert ".claude/commands/retired.md" not in vault.read_baseline(made)


def test_the_baseline_records_the_version_that_wrote_it(made):
    vault.upgrade(made, version="9.9.9")

    written = json.loads((made / ".crate" / "baseline.json").read_text(encoding="utf-8"))
    assert written["crate_version"] == "9.9.9"


# --------------------------------------------------------------------------------------
# CONVENTIONS.md — the file the engine hands over and never takes back
# --------------------------------------------------------------------------------------


def test_upgrade_seeds_conventions_into_a_vault_that_predates_it(made):
    (made / "CONVENTIONS.md").unlink()

    report = vault.upgrade(made, version=__version__)

    assert report.seeded == ["CONVENTIONS.md"]
    assert (made / "CONVENTIONS.md").is_file()


def test_a_convention_survives_an_upgrade(made):
    """The headline promise of this deliverable."""
    conventions = made / "CONVENTIONS.md"
    conventions.write_text("- Source pages are titled by date.\n", encoding="utf-8")
    age(made)

    vault.upgrade(made, version=__version__)

    assert conventions.read_text(encoding="utf-8") == "- Source pages are titled by date.\n"
    assert vault.upgrade(made, version=__version__).seeded == []


def test_conventions_is_seeded_even_when_the_schema_is_left_alone(made):
    """Ordering matters: the message says to move local rules into a file that has to exist."""
    (made / "CONVENTIONS.md").unlink()
    schema = made / "CLAUDE.md"
    edit(schema)

    report = vault.upgrade(made, version=__version__)

    assert report.edited == ["CLAUDE.md"]
    assert (made / "CONVENTIONS.md").is_file()


# --------------------------------------------------------------------------------------
# the CLI surface
# --------------------------------------------------------------------------------------


def test_the_cli_says_when_there_is_nothing_to_do(made):
    result = runner.invoke(app, ["upgrade", str(made)])

    assert result.exit_code == 0
    assert "Already up to date" in result.output


def test_the_cli_reports_an_edited_schema_and_points_at_conventions(made):
    schema = made / "CLAUDE.md"
    edit(schema)

    result = runner.invoke(app, ["upgrade", str(made)])

    assert result.exit_code == 0
    assert "CLAUDE.md" in result.output
    assert "CONVENTIONS.md" in result.output
    assert "--adopt" in result.output
    assert "Already up to date" not in result.output


def test_the_cli_reports_a_schema_it_has_no_record_of(made):
    (made / "CLAUDE.md").write_text("an older schema\n", encoding="utf-8")
    (made / ".crate" / "baseline.json").unlink()

    result = runner.invoke(app, ["upgrade", str(made)])

    assert result.exit_code == 0
    assert "no record" in result.output
    assert "--adopt" in result.output


def test_the_cli_adopt_flag_takes_the_shipped_schema(made):
    schema = made / "CLAUDE.md"
    edit(schema)

    result = runner.invoke(app, ["upgrade", str(made), "--adopt"])

    assert result.exit_code == 0
    assert "my own house rule" not in schema.read_text(encoding="utf-8")


def test_the_cli_refuses_a_directory_that_is_not_a_vault(tmp_path):
    result = runner.invoke(app, ["upgrade", str(tmp_path)])

    assert result.exit_code == 1
    assert "not a crate vault" in result.output
