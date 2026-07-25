"""Tests for the mechanical half of `/ingest`: pending, new, index, log.

Fixtures are synthetic and built in tmp_path — no real vault content reaches this repo.
"""

import os
from datetime import datetime

import pytest
from typer.testing import CliRunner

from crate_wiki import vault, wiki
from crate_wiki.cli import app

runner = CliRunner()

# Staleness compares a raw file's mtime against its page's `updated:`, so a fixture that writes
# the raw file *now* and dates the page in the past is stale the moment the real clock passes
# that date. Pin both to the same fixed day instead: these tests are about the ledger, not about
# what day it happens to be when they run.
TODAY = "2026-07-20"
RAW = "raw/sessions/claude-code/2026-07-20-abcd1234.md"


@pytest.fixture
def made(tmp_path):
    """An empty personal vault, plus one captured-looking raw session dated TODAY."""
    target = tmp_path / "vault"
    result = runner.invoke(app, ["init", str(target), "--scope", "personal"])
    assert result.exit_code == 0, result.output
    raw = target / RAW
    raw.write_text("---\nsource: claude-code\n---\n\n# branch · 2026-07-20\n", encoding="utf-8")
    touch(raw, TODAY)
    return target


def touch(path, day):
    """Set a file's mtime to midday on `day`, so staleness doesn't depend on the wall clock."""
    stamp = datetime.fromisoformat(f"{day}T12:00:00").timestamp()
    os.utime(path, (stamp, stamp))


def source_page(target, title="Fake Session", raw=RAW):
    return wiki.new_page(target, "source", title, raw=raw, today=TODAY)


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
    """A resumed session rewrites its card, so an ingested source can outrun the page about it."""
    source_page(made)
    touch(made / RAW, "2026-07-24")

    assert [item.status for item in wiki.pending(made)] == ["stale"]


def test_all_lists_ingested_sources_without_calling_them_stale(made):
    source_page(made)
    assert [item.status for item in wiki.pending(made, include_all=True)] == ["ingested"]


def test_wikilink_sources_on_other_page_types_never_match_a_raw_file(made):
    """Non-source pages carry `[[Page]]` in `sources:`, which must not shadow a raw path."""
    wiki.new_page(made, "concept", "Session Parser", today=TODAY)
    assert len(wiki.pending(made)) == 1


def test_pending_on_a_directory_that_is_not_a_vault(tmp_path):
    with pytest.raises(vault.VaultError):
        wiki.pending(tmp_path)


# --------------------------------------------------------------------------------------
# new — scaffolding
# --------------------------------------------------------------------------------------


def test_a_scaffolded_page_gets_the_title_as_filename_and_h1(made):
    path = wiki.new_page(made, "concept", "Session Parser", today=TODAY)

    assert path == made / "wiki" / "concepts" / "Session Parser.md"
    assert "# Session Parser" in path.read_text(encoding="utf-8")


def test_dates_are_stamped_in_the_frontmatter(made):
    path = wiki.new_page(made, "entity", "crate-wiki", today=TODAY)
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


def test_a_scaffolded_page_ships_no_placeholder_wikilink(made):
    """The skeletons carry `[[Source Page Name]]` as an example; shipping it is a dead link."""
    path = wiki.new_page(made, "concept", "Session Parser", today=TODAY)
    text = path.read_text(encoding="utf-8")

    assert "Source Page Name" not in text
    assert wiki.parse_list(wiki.read_frontmatter(text)["sources"]) == ()


def test_a_source_page_without_raw_is_refused(made):
    """Without it there's no ledger entry, so the next /ingest would duplicate the page."""
    with pytest.raises(vault.VaultError, match="--raw"):
        wiki.new_page(made, "source", "Fake Session")


def test_scaffolding_never_overwrites_an_existing_page(made):
    wiki.new_page(made, "concept", "Session Parser", today=TODAY)
    with pytest.raises(vault.VaultError, match="already exists"):
        wiki.new_page(made, "concept", "Session Parser", today=TODAY)


@pytest.mark.parametrize("title", ["a/b", "../escape", "a[b]", "a|b", "a#b", "a?b", "a:b", ""])
def test_titles_that_would_break_a_filename_or_a_wikilink_are_refused(made, title):
    """`?` and `:` among them: a synthesis title can't be a question, so /ask uses a claim."""
    with pytest.raises(vault.VaultError):
        wiki.new_page(made, "concept", title, today=TODAY)


def test_an_unknown_page_type_is_refused(made):
    with pytest.raises(vault.VaultError, match="unknown page type"):
        wiki.new_page(made, "essay", "Whatever", today=TODAY)


def test_scaffolding_uses_the_vaults_template_not_the_packages(made):
    """`.crate/templates/` is copied into the vault so it can be customised there."""
    (made / ".crate" / "templates" / "concept.md").write_text(
        "---\ntype: concept\ncreated: YYYY-MM-DD\n---\n\n# Title\n\nhouse style\n",
        encoding="utf-8",
    )
    path = wiki.new_page(made, "concept", "Session Parser", today=TODAY)

    assert "house style" in path.read_text(encoding="utf-8")


def test_a_date_in_the_body_is_not_rewritten(made):
    (made / ".crate" / "templates" / "concept.md").write_text(
        "---\ntype: concept\ncreated: YYYY-MM-DD\n---\n\n# Title\n\nShipped on YYYY-MM-DD.\n",
        encoding="utf-8",
    )
    body = wiki.new_page(made, "concept", "X", today=TODAY).read_text(encoding="utf-8")

    assert "Shipped on YYYY-MM-DD." in body
    assert "created: 2026-07-20" in body


# --------------------------------------------------------------------------------------
# extend — the two mechanical edits of absorbing a source
# --------------------------------------------------------------------------------------


def test_extending_moves_updated_to_today_and_leaves_created_alone(made):
    page = wiki.new_page(made, "concept", "Session Parser", today=TODAY)

    wiki.extend_page(made, "Session Parser", today="2026-07-24")

    fields = wiki.read_frontmatter(page.read_text(encoding="utf-8"))
    assert fields["updated"] == "2026-07-24"
    assert fields["created"] == "2026-07-20"


def test_a_new_source_is_appended_to_the_ledger(made):
    page = source_page(made)

    wiki.extend_page(made, "Fake Session", source="raw/sessions/claude-code/later.md", today="x")

    sources = wiki.parse_list(wiki.read_frontmatter(page.read_text(encoding="utf-8"))["sources"])
    assert sources == (RAW, "raw/sessions/claude-code/later.md")


def test_a_source_already_listed_is_not_added_twice(made):
    page = source_page(made)

    wiki.extend_page(made, "Fake Session", source=RAW, today=TODAY)

    sources = wiki.parse_list(wiki.read_frontmatter(page.read_text(encoding="utf-8"))["sources"])
    assert sources == (RAW,)


def test_extending_twice_reports_no_change_the_second_time(made):
    wiki.new_page(made, "concept", "Session Parser", today=TODAY)

    _, first = wiki.extend_page(made, "Session Parser", source="[[A]]", today="2026-07-24")
    _, second = wiki.extend_page(made, "Session Parser", source="[[A]]", today="2026-07-24")

    assert first is True
    assert second is False


def test_the_body_of_the_page_is_untouched(made):
    page = wiki.new_page(made, "concept", "Session Parser", today=TODAY)
    body = page.read_text(encoding="utf-8").split("---", 2)[2]

    wiki.extend_page(made, "Session Parser", source="[[A]]", today="2026-07-24")

    assert page.read_text(encoding="utf-8").split("---", 2)[2] == body


def test_a_wikilink_source_is_recorded_verbatim(made):
    """The `·` in the source-page naming convention must survive being written into frontmatter."""
    wiki.new_page(made, "concept", "Session Parser", today=TODAY)
    link = "[[Session · 2026-07-19 · d2-session-parser]]"

    wiki.extend_page(made, "Session Parser", source=link, today="2026-07-24")

    page = made / "wiki" / "concepts" / "Session Parser.md"
    assert wiki.parse_list(wiki.read_frontmatter(page.read_text(encoding="utf-8"))["sources"]) == (
        link,
    )


def test_an_unknown_title_is_refused_rather_than_created(made):
    """Creating here would hide a typo — that's `crate new`'s job, not this one's."""
    with pytest.raises(vault.VaultError, match="no page called"):
        wiki.extend_page(made, "Never Written", today="2026-07-24")

    assert not (made / "wiki" / "concepts" / "Never Written.md").exists()


def test_a_title_held_by_two_page_types_is_refused_rather_than_guessed(made):
    wiki.new_page(made, "concept", "Ambiguous", today=TODAY)
    wiki.new_page(made, "entity", "Ambiguous", today=TODAY)

    with pytest.raises(vault.VaultError, match="more than one"):
        wiki.extend_page(made, "Ambiguous", today="2026-07-24")


def test_a_title_given_in_wikilink_form_still_resolves(made):
    wiki.new_page(made, "concept", "Session Parser", today=TODAY)

    path, _ = wiki.extend_page(made, "[[Session Parser]]", today="2026-07-24")

    assert path.stem == "Session Parser"


def test_a_page_without_frontmatter_is_refused(made):
    (made / "wiki" / "concepts" / "Bare.md").write_text("# Bare\n", encoding="utf-8")

    with pytest.raises(vault.VaultError, match="no frontmatter"):
        wiki.extend_page(made, "Bare", today="2026-07-24")


def test_extend_through_the_cli(made):
    wiki.new_page(made, "concept", "Session Parser", today=TODAY)

    result = runner.invoke(
        app, ["extend", "Session Parser", "--source", "[[A]]", "--vault", str(made)]
    )

    assert result.exit_code == 0
    assert "Session Parser.md" in result.output


def test_the_cli_refuses_an_unknown_page(made):
    result = runner.invoke(app, ["extend", "Never Written", "--vault", str(made)])

    assert result.exit_code == 1
    assert "no page called" in result.output


# --------------------------------------------------------------------------------------
# synthesis — the mechanics /ask reuses, and the guarantees it leans on (ADR-0011)
# --------------------------------------------------------------------------------------


def test_a_synthesis_scaffolds_with_the_title_as_h1_and_an_empty_ledger(made):
    """/ask adds no code: `crate new synthesis` scaffolds the page like any other type."""
    path = wiki.new_page(made, "synthesis", "Capture stays free by running on a hook", today=TODAY)
    text = path.read_text(encoding="utf-8")

    assert path == made / "wiki" / "syntheses" / "Capture stays free by running on a hook.md"
    assert "# Capture stays free by running on a hook" in text
    assert wiki.parse_list(wiki.read_frontmatter(text)["sources"]) == ()


def test_a_synthesis_records_the_wiki_pages_it_drew_from(made):
    """A synthesis's `sources:` is provenance too — wiki pages, recorded by the same `extend`."""
    wiki.new_page(made, "synthesis", "Capture stays free", today=TODAY)

    wiki.extend_page(made, "Capture stays free", source="[[Session Parser]]", today=TODAY)
    wiki.extend_page(made, "Capture stays free", source="[[Stop Hook]]", today=TODAY)

    page = made / "wiki" / "syntheses" / "Capture stays free.md"
    sources = wiki.parse_list(wiki.read_frontmatter(page.read_text(encoding="utf-8"))["sources"])
    assert sources == ("[[Session Parser]]", "[[Stop Hook]]")


def test_a_synthesis_source_never_makes_a_raw_file_look_ingested(made):
    """The ledger reads only wiki/sources/, so a synthesis citing pages can't shadow a raw file."""
    wiki.new_page(made, "synthesis", "Capture stays free", today=TODAY)
    wiki.extend_page(made, "Capture stays free", source="[[Session Parser]]", today=TODAY)

    assert [item.path for item in wiki.pending(made)] == [RAW]
    assert wiki.ingested(made) == {}


def test_the_index_lists_a_synthesis_under_its_own_section(made):
    summarise(
        wiki.new_page(made, "synthesis", "Capture stays free", today=TODAY),
        "Runs on a hook, so it costs no tokens.",
    )
    text = wiki.reindex(made).read_text(encoding="utf-8")

    entry = "- [[Capture stays free]] — Runs on a hook, so it costs no tokens."
    assert f"## Syntheses\n\n{entry}" in text


# --------------------------------------------------------------------------------------
# index — derived, never authored
# --------------------------------------------------------------------------------------


def summarise(path, summary):
    text = path.read_text(encoding="utf-8").replace("summary:", f"summary: {summary}", 1)
    path.write_text(text, encoding="utf-8")


def test_the_index_lists_each_page_under_its_section_with_its_summary(made):
    summarise(source_page(made), "A synthetic session.")
    summarise(
        wiki.new_page(made, "concept", "Session Parser", today=TODAY),
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
    page = wiki.new_page(made, "concept", "Session Parser", today=TODAY)
    wiki.reindex(made)
    page.unlink()

    assert "[[Session Parser]]" not in wiki.reindex(made).read_text(encoding="utf-8")


def test_a_fresh_vaults_index_is_what_regenerating_would_produce(made):
    """`crate init` generates the index rather than shipping a skeleton, so the two can't drift."""
    before = (made / "index.md").read_text(encoding="utf-8")
    assert wiki.reindex(made).read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------------------
# fmt — one line per paragraph, and nothing else touched
# --------------------------------------------------------------------------------------


def test_a_hard_wrapped_paragraph_becomes_one_line():
    assert wiki.reflow("One two\nthree four\nfive.\n") == "One two three four five.\n"


def test_paragraphs_stay_separate():
    assert wiki.reflow("One\ntwo.\n\nThree\nfour.\n") == "One two.\n\nThree four.\n"


def test_frontmatter_is_never_joined():
    text = "---\ntype: concept\nsummary: A line.\n---\n\nOne\ntwo.\n"
    assert wiki.reflow(text) == "---\ntype: concept\nsummary: A line.\n---\n\nOne two.\n"


def test_a_fenced_code_block_is_untouched():
    text = "Prose\nhere.\n\n```python\nx = 1\ny = 2\n```\n\nMore\nprose.\n"
    result = wiki.reflow(text)

    assert "```python\nx = 1\ny = 2\n```" in result
    assert "Prose here." in result
    assert "More prose." in result


def test_a_table_is_untouched():
    text = "| a | b |\n|---|---|\n| 1 | 2 |\n"
    assert wiki.reflow(text) == text


def test_headings_and_blockquotes_are_untouched():
    text = "# Title\n\n> quoted\n> lines\n"
    assert wiki.reflow(text) == text


def test_list_items_keep_one_line_each_and_absorb_continuations():
    text = "- first item\n  wrapped on.\n- second item\n"
    assert wiki.reflow(text) == "- first item wrapped on.\n- second item\n"


def test_a_nested_list_item_keeps_its_indentation():
    text = "- outer\n  - inner item\n    wrapped.\n"
    assert wiki.reflow(text) == "- outer\n  - inner item wrapped.\n"


def test_an_explicit_hard_break_survives():
    """Two trailing spaces mean the author wanted a break — reflowing must not eat it."""
    result = wiki.reflow("line one  \nline two\nline three.\n")

    assert result == "line one  \nline two line three.\n"


def test_a_backslash_hard_break_survives():
    assert wiki.reflow("line one\\\nline two.\n") == "line one\\\nline two.\n"


def test_reflowing_is_idempotent():
    text = "One\ntwo.\n\n- item\n  wrapped\n\n```\ncode\n```\n"
    once = wiki.reflow(text)

    assert wiki.reflow(once) == once


def test_an_already_flat_page_is_returned_unchanged():
    text = "---\ntype: concept\n---\n\n# Title\n\nAll on one line already.\n"
    assert wiki.reflow(text) == text


def test_a_wikilink_spanning_a_line_break_is_repaired(made):
    """The case that matters: a link broken across lines resolves once the paragraph is one line."""
    result = wiki.reflow("See the [[Session\nParser]] page.\n")

    assert result == "See the [[Session Parser]] page.\n"


def test_format_pages_rewrites_wiki_pages_and_reports_them(made):
    page = wiki.new_page(made, "concept", "Session Parser", today=TODAY)
    page.write_text("---\ntype: concept\n---\n\n# X\n\nOne\ntwo.\n", encoding="utf-8")

    changed = wiki.format_pages(made)

    assert changed == [page]
    assert "One two." in page.read_text(encoding="utf-8")


def test_format_pages_reports_nothing_when_every_page_is_already_flat(made):
    wiki.new_page(made, "concept", "Session Parser", today=TODAY)
    wiki.format_pages(made)

    assert wiki.format_pages(made) == []


def test_fmt_through_the_cli(made):
    page = wiki.new_page(made, "concept", "Session Parser", today=TODAY)
    page.write_text("---\ntype: concept\n---\n\n# X\n\nOne\ntwo.\n", encoding="utf-8")

    result = runner.invoke(app, ["fmt", "--vault", str(made)])

    assert result.exit_code == 0
    assert "Session Parser.md" in result.output


# --------------------------------------------------------------------------------------
# log — append-only
# --------------------------------------------------------------------------------------


def test_a_log_entry_has_the_documented_shape(made):
    line = wiki.append_log(made, "ingest", "Fake Session", today=TODAY)

    assert line == "## [2026-07-20] ingest | Fake Session"
    assert line in (made / "log.md").read_text(encoding="utf-8")


def test_an_ask_entry_reads_as_an_ask(made):
    """The operation is a free-form column, so /ask logs with `ask` and no new code."""
    line = wiki.append_log(made, "ask", "Capture stays free", today=TODAY)

    assert line == "## [2026-07-20] ask | Capture stays free"


def test_appending_never_disturbs_what_is_already_there(made):
    before = (made / "log.md").read_text(encoding="utf-8")
    wiki.append_log(made, "ingest", "One", today=TODAY)
    wiki.append_log(made, "ingest", "Two", today="2026-07-21")
    after = (made / "log.md").read_text(encoding="utf-8")

    assert after.startswith(before)
    assert after.index("| One") < after.index("| Two")


def test_entries_are_separated_by_a_blank_line(made):
    wiki.append_log(made, "ingest", "One", today=TODAY)
    wiki.append_log(made, "ingest", "Two", today="2026-07-21")
    text = (made / "log.md").read_text(encoding="utf-8")

    assert "\n\n## [2026-07-21] ingest | Two\n" in text


def test_a_newline_in_a_title_cannot_forge_a_second_entry(made):
    """One operation, one line. A `##` surviving inline isn't a heading, so it isn't an entry."""
    before = len((made / "log.md").read_text(encoding="utf-8").splitlines())
    wiki.append_log(made, "ingest", "One\n## [2026-07-20] ingest | Forged", today=TODAY)
    lines = (made / "log.md").read_text(encoding="utf-8").splitlines()

    assert len([line for line in lines if line.startswith("## ")]) == 2  # init, plus this one
    assert len(lines) == before + 2  # a blank separator and the entry


def test_an_empty_title_is_refused(made):
    with pytest.raises(vault.VaultError):
        wiki.append_log(made, "ingest", "   ", today=TODAY)


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
