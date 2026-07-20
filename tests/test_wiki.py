"""Tests for the mechanical half of `/ingest`: pending, new, index, log.

Fixtures are synthetic and built in tmp_path — no real vault content reaches this repo.
"""

import pytest
from typer.testing import CliRunner

from crate_wiki import vault, wiki
from crate_wiki.cli import app

runner = CliRunner()


@pytest.fixture
def made(tmp_path):
    """An empty personal vault, plus one captured-looking raw session."""
    target = tmp_path / "vault"
    result = runner.invoke(app, ["init", str(target), "--scope", "personal"])
    assert result.exit_code == 0, result.output
    raw = target / "raw" / "sessions" / "claude-code" / "2026-07-20-abcd1234.md"
    raw.write_text("---\nsource: claude-code\n---\n\n# branch · 2026-07-20\n", encoding="utf-8")
    return target


RAW = "raw/sessions/claude-code/2026-07-20-abcd1234.md"


def source_page(target, title="Fake Session", raw=RAW):
    return wiki.new_page(target, "source", title, raw=raw, today="2026-07-20")


# --------------------------------------------------------------------------------------
# frontmatter
# --------------------------------------------------------------------------------------


def test_frontmatter_reads_scalars_and_stops_at_the_closing_fence():
    text = "---\ntype: source\nsummary: A line.\n---\n\n# Title\n\nnot: frontmatter\n"
    assert wiki.read_frontmatter(text) == {"type": "source", "summary": "A line."}


def test_a_page_without_frontmatter_yields_no_fields_rather_than_raising():
    assert wiki.read_frontmatter("# Just a heading\n") == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('["a.md", "b.md"]', ("a.md", "b.md")),
        ("[]", ()),
        ("", ()),
        ("a.md", ("a.md",)),
    ],
)
def test_list_values_parse(raw, expected):
    assert wiki.parse_list(raw) == expected


# --------------------------------------------------------------------------------------
# pending — the ledger, and therefore idempotency
# --------------------------------------------------------------------------------------


def test_a_captured_session_starts_out_pending(made):
    assert [item.path for item in wiki.pending(made)] == [
        "raw/sessions/claude-code/2026-07-20-abcd1234.md"
    ]


def test_ingesting_a_source_removes_it_from_pending(made):
    source_page(made)
    assert wiki.pending(made) == []


def test_re_running_ingest_finds_nothing_however_many_times(made):
    source_page(made)
    assert wiki.pending(made) == []
    assert wiki.pending(made) == []


def test_deleting_the_source_page_makes_the_raw_file_pending_again(made):
    page = source_page(made)
    page.unlink()
    assert len(wiki.pending(made)) == 1


def test_private_sections_never_appear(made):
    """ADR-0006: the gitignore keeps them off a remote; this keeps them out of wiki/."""
    (made / "raw" / "journal" / "2026-07-20.md").write_text("private\n", encoding="utf-8")
    assert all("journal" not in item.path for item in wiki.pending(made))


def test_a_raw_file_newer_than_its_page_is_stale(made):
    source_page(made)
    page = made / "wiki" / "sources" / "Fake Session.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace("updated: 2026-07-20", "updated: 2020-01-01"),
        encoding="utf-8",
    )
    assert [item.status for item in wiki.pending(made)] == ["stale"]


def test_all_lists_ingested_sources_without_calling_them_stale(made):
    source_page(made)
    assert [item.status for item in wiki.pending(made, include_all=True)] == ["ingested"]


def test_wikilink_sources_on_other_page_types_never_match_a_raw_file(made):
    """Non-source pages carry `[[Page]]` in `sources:`, which must not shadow a raw path."""
    wiki.new_page(made, "concept", "Session Parser", today="2026-07-20")
    assert len(wiki.pending(made)) == 1


def test_pending_on_a_directory_that_is_not_a_vault(tmp_path):
    with pytest.raises(vault.VaultError):
        wiki.pending(tmp_path)


# --------------------------------------------------------------------------------------
# new — scaffolding
# --------------------------------------------------------------------------------------


def test_a_scaffolded_page_gets_the_title_as_filename_and_h1(made):
    path = wiki.new_page(made, "concept", "Session Parser", today="2026-07-20")

    assert path == made / "wiki" / "concepts" / "Session Parser.md"
    assert "# Session Parser" in path.read_text(encoding="utf-8")


def test_dates_are_stamped_in_the_frontmatter(made):
    path = wiki.new_page(made, "entity", "crate-wiki", today="2026-07-20")
    fields = wiki.read_frontmatter(path.read_text(encoding="utf-8"))

    assert fields["created"] == "2026-07-20"
    assert fields["updated"] == "2026-07-20"
    assert fields["type"] == "entity"


def test_a_source_page_records_the_raw_path_it_came_from(made):
    path = source_page(made)
    fields = wiki.read_frontmatter(path.read_text(encoding="utf-8"))

    assert wiki.parse_list(fields["sources"]) == (
        "raw/sessions/claude-code/2026-07-20-abcd1234.md",
    )


def test_a_source_page_without_raw_is_refused(made):
    """Without it there's no ledger entry, so the next /ingest would duplicate the page."""
    with pytest.raises(vault.VaultError, match="--raw"):
        wiki.new_page(made, "source", "Fake Session")


def test_scaffolding_never_overwrites_an_existing_page(made):
    wiki.new_page(made, "concept", "Session Parser", today="2026-07-20")
    with pytest.raises(vault.VaultError, match="already exists"):
        wiki.new_page(made, "concept", "Session Parser", today="2026-07-20")


@pytest.mark.parametrize("title", ["a/b", "../escape", "a[b]", "a|b", "a#b", ""])
def test_titles_that_would_break_a_filename_or_a_wikilink_are_refused(made, title):
    with pytest.raises(vault.VaultError):
        wiki.new_page(made, "concept", title, today="2026-07-20")


def test_an_unknown_page_type_is_refused(made):
    with pytest.raises(vault.VaultError, match="unknown page type"):
        wiki.new_page(made, "essay", "Whatever", today="2026-07-20")


def test_scaffolding_uses_the_vaults_template_not_the_packages(made):
    """`.crate/templates/` is copied into the vault so it can be customised there."""
    (made / ".crate" / "templates" / "concept.md").write_text(
        "---\ntype: concept\ncreated: YYYY-MM-DD\n---\n\n# Title\n\nhouse style\n",
        encoding="utf-8",
    )
    path = wiki.new_page(made, "concept", "Session Parser", today="2026-07-20")

    assert "house style" in path.read_text(encoding="utf-8")


def test_a_date_in_the_body_is_not_rewritten(made):
    (made / ".crate" / "templates" / "concept.md").write_text(
        "---\ntype: concept\ncreated: YYYY-MM-DD\n---\n\n# Title\n\nShipped on YYYY-MM-DD.\n",
        encoding="utf-8",
    )
    body = wiki.new_page(made, "concept", "X", today="2026-07-20").read_text(encoding="utf-8")

    assert "Shipped on YYYY-MM-DD." in body
    assert "created: 2026-07-20" in body


# --------------------------------------------------------------------------------------
# index — derived, never authored
# --------------------------------------------------------------------------------------


def summarise(path, summary):
    text = path.read_text(encoding="utf-8").replace("summary:", f"summary: {summary}", 1)
    path.write_text(text, encoding="utf-8")


def test_the_index_lists_each_page_under_its_section_with_its_summary(made):
    summarise(source_page(made), "A synthetic session.")
    summarise(
        wiki.new_page(made, "concept", "Session Parser", today="2026-07-20"),
        "Discards more than it converts.",
    )
    text = wiki.reindex(made).read_text(encoding="utf-8")

    assert "## Sources\n\n- [[Fake Session]] — A synthetic session." in text
    assert "## Concepts\n\n- [[Session Parser]] — Discards more than it converts." in text


def test_regenerating_the_index_is_idempotent(made):
    summarise(source_page(made), "A synthetic session.")
    first = wiki.reindex(made).read_text(encoding="utf-8")
    second = wiki.reindex(made).read_text(encoding="utf-8")

    assert first == second


def test_a_page_with_no_summary_is_still_listed(made):
    source_page(made)
    text = wiki.reindex(made).read_text(encoding="utf-8")

    assert "[[Fake Session]]" in text
    assert "no summary" in text


def test_a_page_with_broken_frontmatter_is_still_listed(made):
    """Being wrong in the index is recoverable; being absent from it is the failure."""
    page = made / "wiki" / "concepts" / "Half Written.md"
    page.write_text("# Half Written\n", encoding="utf-8")
    assert "[[Half Written]]" in wiki.reindex(made).read_text(encoding="utf-8")


def test_prose_above_the_first_section_survives_regeneration(made):
    index = made / "index.md"
    index.write_text("# Index\n\nMy own note about this wiki.\n\n## Sources\n", encoding="utf-8")
    wiki.reindex(made)

    assert "My own note about this wiki." in index.read_text(encoding="utf-8")


def test_a_page_removed_from_disk_leaves_the_index(made):
    page = wiki.new_page(made, "concept", "Session Parser", today="2026-07-20")
    wiki.reindex(made)
    page.unlink()

    assert "[[Session Parser]]" not in wiki.reindex(made).read_text(encoding="utf-8")


def test_a_fresh_vaults_index_is_what_regenerating_would_produce(made):
    """`crate init` generates the index rather than shipping a skeleton, so the two can't drift."""
    before = (made / "index.md").read_text(encoding="utf-8")
    assert wiki.reindex(made).read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------------------
# log — append-only
# --------------------------------------------------------------------------------------


def test_a_log_entry_has_the_documented_shape(made):
    line = wiki.append_log(made, "ingest", "Fake Session", today="2026-07-20")

    assert line == "## [2026-07-20] ingest | Fake Session"
    assert line in (made / "log.md").read_text(encoding="utf-8")


def test_appending_never_disturbs_what_is_already_there(made):
    before = (made / "log.md").read_text(encoding="utf-8")
    wiki.append_log(made, "ingest", "One", today="2026-07-20")
    wiki.append_log(made, "ingest", "Two", today="2026-07-21")
    after = (made / "log.md").read_text(encoding="utf-8")

    assert after.startswith(before)
    assert after.index("| One") < after.index("| Two")


def test_entries_are_separated_by_a_blank_line(made):
    wiki.append_log(made, "ingest", "One", today="2026-07-20")
    wiki.append_log(made, "ingest", "Two", today="2026-07-21")
    text = (made / "log.md").read_text(encoding="utf-8")

    assert "\n\n## [2026-07-21] ingest | Two\n" in text


def test_a_newline_in_a_title_cannot_forge_a_second_entry(made):
    """One operation, one line. A `##` surviving inline isn't a heading, so it isn't an entry."""
    before = len((made / "log.md").read_text(encoding="utf-8").splitlines())
    wiki.append_log(made, "ingest", "One\n## [2026-07-20] ingest | Forged", today="2026-07-20")
    lines = (made / "log.md").read_text(encoding="utf-8").splitlines()

    assert len([line for line in lines if line.startswith("## ")]) == 2  # init, plus this one
    assert len(lines) == before + 2  # a blank separator and the entry


def test_an_empty_title_is_refused(made):
    with pytest.raises(vault.VaultError):
        wiki.append_log(made, "ingest", "   ", today="2026-07-20")


# --------------------------------------------------------------------------------------
# the CLI surface
# --------------------------------------------------------------------------------------


def test_pending_prints_a_bare_path_for_a_new_source(made):
    result = runner.invoke(app, ["pending", "--vault", str(made)])

    assert result.exit_code == 0
    assert result.output.strip() == "raw/sessions/claude-code/2026-07-20-abcd1234.md"


def test_pending_says_nothing_and_succeeds_when_there_is_nothing_to_do(made):
    source_page(made)
    result = runner.invoke(app, ["pending", "--vault", str(made)])

    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_the_cli_reports_a_bad_vault_rather_than_a_traceback(tmp_path):
    result = runner.invoke(app, ["pending", "--vault", str(tmp_path)])

    assert result.exit_code == 1
    assert "not a crate vault" in result.output


def test_new_prints_the_path_it_wrote(made):
    result = runner.invoke(app, ["new", "concept", "Session Parser", "--vault", str(made)])

    assert result.exit_code == 0
    assert result.output.strip().endswith("Session Parser.md")


def test_log_through_the_cli_appends_one_entry(made):
    result = runner.invoke(app, ["log", "ingest", "--title", "Fake Session", "--vault", str(made)])

    assert result.exit_code == 0
    assert "] ingest | Fake Session" in (made / "log.md").read_text(encoding="utf-8")
