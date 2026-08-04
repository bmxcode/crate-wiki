"""Tests for `crate lint` — the five checks with a single right answer, and the ones it won't make.

Fixtures are synthetic and built in tmp_path; no real vault content reaches this repo.

Two things here are load-bearing rather than incidental. The false positives from issue #9 are
pinned directly, because a checker that fires on the text the engine itself ships is one nobody
keeps running. And staleness is pinned *absent*: a raw file that changed after the page written
from it is `crate pending`'s answer (ADR-0016, ADR-0017), so a finding here would be a second
answer to a question that already has one.
"""

import pytest
from typer.testing import CliRunner

from crate_wiki import lint, vault, wiki
from crate_wiki.cli import app

runner = CliRunner()

TODAY = "2026-07-20"
RAW = "raw/sessions/claude-code/2026-07-20-abcd1234.md"
JOURNAL = "raw/journal/2026-07-20.md"

# Verbatim from the index.md this engine ships. It reads as a dead link to a naive sweep and is
# not one, because Obsidian doesn't linkify a code span.
SHIPPED_EXAMPLE = (
    "Every page in this wiki, exactly once, under its type, as `[[Page Name]] — one line`."
)


@pytest.fixture
def made(tmp_path):
    """A personal vault with one captured-looking raw card, indexed and clean."""
    return _vault(tmp_path, "personal")


@pytest.fixture
def work(tmp_path):
    """A work vault — same shape, but its preset defines no private section."""
    return _vault(tmp_path, "work")


def _vault(tmp_path, scope):
    target = tmp_path / scope
    result = runner.invoke(app, ["init", str(target), "--scope", scope])
    assert result.exit_code == 0, result.output
    raw(target, RAW)
    return target


def raw(target, relative, text="---\nsource: claude-code\n---\n\n# branch · 2026-07-20\n"):
    """Write a raw file, creating its section directory. Returns the vault-relative path."""
    path = target / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return relative


def page(target, kind, title, body="", *, source=None):
    """Scaffold a page from the vault's own template, then append `body` to it."""
    path = wiki.new_page(target, kind, title, raw=source, today=TODAY)
    if body:
        path.write_text(path.read_text(encoding="utf-8") + body, encoding="utf-8")
    return path


def rewrite_index(target, before, after):
    """Edit the hand-written half of index.md, the part a regeneration preserves."""
    index = target / "index.md"
    index.write_text(index.read_text(encoding="utf-8").replace(before, after), encoding="utf-8")


def settle(target):
    """Regenerate the index, so a test about one check isn't also a test about `index-stale`."""
    wiki.reindex(target)


def findings(target):
    return [(item.path, item.check, item.detail) for item in lint.check(target)]


def checks(target):
    return sorted({item.check for item in lint.check(target)})


# --------------------------------------------------------------------------------------
# a vault the engine shipped
# --------------------------------------------------------------------------------------


def test_a_freshly_created_vault_lints_clean(made):
    """A checker that cries wolf on the vault the engine itself ships is one you turn off."""
    assert lint.check(made) == []


def test_a_freshly_created_work_vault_lints_clean(work):
    assert lint.check(work) == []


def test_the_shipped_index_header_holds_no_link_at_all(made):
    """`[[Page Name]]` in the shipped header sits in a code span, so it is not a link."""
    assert lint.wikilinks(vault.template_text("vault", "index.md")) == []
    assert lint.wikilinks(wiki.index_header(made)) == []


# --------------------------------------------------------------------------------------
# dead-link — and the three false positives from #9
# --------------------------------------------------------------------------------------


def test_a_link_to_a_page_that_exists_is_silent(made):
    page(made, "concept", "Session Parser")
    page(made, "source", "A Session", "\nBuilt on the [[Session Parser]].\n", source=RAW)
    settle(made)

    assert "dead-link" not in checks(made)


def test_a_link_to_a_page_that_does_not_exist_is_reported(made):
    page(made, "source", "A Session", "\nBuilt on the [[Card Model]].\n", source=RAW)
    settle(made)

    assert findings(made) == [("wiki/sources/A Session.md", "dead-link", "[[Card Model]]")]


def test_a_wikilink_inside_a_code_span_is_not_a_link(made):
    page(made, "source", "A Session", f"\n{SHIPPED_EXAMPLE}\n", source=RAW)
    settle(made)

    assert lint.check(made) == []


def test_a_wikilink_inside_a_fenced_block_is_not_a_link(made):
    body = "\nThe format is:\n\n```\n- [[Page Name]] — one line\n```\n"
    page(made, "source", "A Session", body, source=RAW)
    settle(made)

    assert lint.check(made) == []


def test_an_unclosed_backtick_run_does_not_swallow_the_rest_of_the_line(made):
    """CommonMark: a run with no matching closer is literal, so a later real span still closes."""
    assert lint.wikilinks("``opener and `[[Card Model]]` after") == []
    assert lint.wikilinks("``opener and [[Card Model]] after") == ["Card Model"]


@pytest.mark.parametrize(
    "written",
    ["[[Session Parser|the parser]]", "[[Session Parser#Design]]", "[[Session Parser#Design|it]]"],
)
def test_the_alias_and_heading_forms_resolve_to_the_page(made, written):
    page(made, "concept", "Session Parser")
    page(made, "source", "A Session", f"\nBuilt on {written}.\n", source=RAW)
    settle(made)

    assert "dead-link" not in checks(made)


def test_a_link_to_a_heading_on_this_page_names_no_target(made):
    page(made, "source", "A Session", "\nSee [[#Design]] below.\n", source=RAW)
    settle(made)

    assert lint.check(made) == []


def test_a_dead_link_in_the_index_header_is_reported(made):
    """The header survives regeneration, so a dead link someone wrote there stays forever."""
    rewrite_index(made, "# Index", "# Index\n\n[[Nope]]")
    settle(made)

    assert ("index.md", "dead-link", "[[Nope]]") in findings(made)


# --------------------------------------------------------------------------------------
# orphan — and the kinds that may legitimately be one
# --------------------------------------------------------------------------------------


def test_a_concept_nothing_links_to_is_an_orphan(made):
    page(made, "concept", "Session Parser")
    settle(made)

    assert findings(made) == [
        ("wiki/concepts/Session Parser.md", "orphan", "no other page links to it")
    ]


def test_a_concept_another_page_links_to_is_not_an_orphan(made):
    page(made, "concept", "Session Parser")
    page(made, "source", "A Session", "\nBuilt on the [[Session Parser]].\n", source=RAW)
    settle(made)

    assert lint.check(made) == []


def test_the_index_does_not_rescue_an_orphan(made):
    """`crate index` links every page by construction, so counting it would find nothing (#9)."""
    page(made, "concept", "Session Parser")
    settle(made)

    assert "[[Session Parser]]" in (made / "index.md").read_text(encoding="utf-8")
    assert "orphan" in checks(made)


def test_a_page_that_only_links_to_itself_is_still_an_orphan(made):
    page(made, "concept", "Session Parser", "\nSee [[Session Parser]].\n")
    settle(made)

    assert "orphan" in checks(made)


def test_a_source_page_nothing_links_to_yet_is_not_an_orphan(made):
    """An early vault is almost all of these, and reporting every one is the cry-wolf failure."""
    page(made, "source", "A Session", source=RAW)
    settle(made)

    assert lint.check(made) == []


def test_a_daily_page_nothing_links_to_is_not_an_orphan(made):
    """A daily page is a leaf by design: it links out to pages, and nothing links back to a date."""
    page(made, "daily", "2026-07-20")
    settle(made)

    assert lint.check(made) == []


# --------------------------------------------------------------------------------------
# index-stale — one comparison, reported once
# --------------------------------------------------------------------------------------


def test_a_new_page_leaves_the_index_stale_until_it_is_regenerated(made):
    page(made, "source", "A Session", source=RAW)

    assert ("index.md", "index-stale", "out of date — run `crate index`") in findings(made)

    settle(made)
    assert "index-stale" not in checks(made)


def test_two_missing_entries_are_still_one_finding(made):
    """The fix for any number of them is the same single command, so N lines would be noise."""
    page(made, "source", "A Session", source=RAW)
    page(made, "source", "Another Session", source=raw(made, "raw/sessions/codex/other.md"))

    assert [item for item in findings(made) if item[1] == "index-stale"] == [
        ("index.md", "index-stale", "out of date — run `crate index`")
    ]


def test_editing_the_index_header_does_not_make_it_stale(made):
    """The header is preserved across regenerations, so changing it is not drift."""
    rewrite_index(made, "# Index", "# My Index")
    settle(made)

    assert "index-stale" not in checks(made)


# --------------------------------------------------------------------------------------
# private-source and missing-source — what a page claims it was built from
# --------------------------------------------------------------------------------------


def test_a_page_citing_a_private_section_is_reported(made):
    """ADR-0006 asks the linter for this by name: `pending` filters private sections out first."""
    page(made, "source", "A Journal Entry", source=raw(made, JOURNAL, "private\n"))
    settle(made)

    assert findings(made) == [("wiki/sources/A Journal Entry.md", "private-source", JOURNAL)]


def test_pending_cannot_see_what_that_check_sees(made):
    """Pinned because it is the whole reason the check exists rather than being a duplicate."""
    page(made, "source", "A Journal Entry", source=raw(made, JOURNAL, "private\n"))
    settle(made)

    assert all(JOURNAL not in item.path for item in wiki.pending(made, include_all=True))
    assert "private-source" in checks(made)


def test_a_daily_page_citing_a_private_section_is_reported_too(made):
    """A daily page's `sources:` is raw card paths (ADR-0012), so it can leak the same way."""
    raw(made, JOURNAL, "private\n")
    page(made, "daily", "2026-07-20")
    wiki.extend_page(made, "2026-07-20", source=JOURNAL, today=TODAY)
    settle(made)

    assert ("wiki/daily/2026-07-20.md", "private-source", JOURNAL) in findings(made)


def test_a_page_citing_a_public_section_is_silent(made):
    page(made, "source", "A Session", source=RAW)
    settle(made)

    assert "private-source" not in checks(made)


def test_a_work_vault_has_no_private_section_to_leak(work):
    """The preset defines none, so the check is correctly incapable of firing (ADR-0001)."""
    assert wiki.private_sections(work) == set()

    page(work, "source", "A Session", source=RAW)
    settle(work)

    assert "private-source" not in checks(work)


def test_a_page_citing_a_raw_file_that_is_not_there_is_reported(made):
    gone = "raw/sessions/claude-code/gone.md"
    page(made, "source", "A Session", source=gone)
    settle(made)

    assert findings(made) == [("wiki/sources/A Session.md", "missing-source", gone)]


def test_a_wikilink_in_sources_is_provenance_and_not_a_raw_path(made):
    """A synthesis cites pages, not files — checked as a link, never looked for under `raw/`."""
    page(made, "concept", "Session Parser", "\nSee [[An Answer]].\n")
    page(made, "synthesis", "An Answer", "\nBuilt on the [[Session Parser]].\n")
    wiki.extend_page(made, "An Answer", source="[[Session Parser]]", today=TODAY)
    settle(made)

    assert lint.check(made) == []


# --------------------------------------------------------------------------------------
# what the linter deliberately does not say
# --------------------------------------------------------------------------------------


def test_a_raw_file_that_merely_changed_is_not_a_finding(made):
    """Staleness belongs to `crate pending`, against the digest the page recorded (ADR-0017).

    A second answer to the same question would be one without the ledger, and the two would
    disagree the first time a card was re-rendered.
    """
    page(made, "source", "A Session", source=RAW)
    settle(made)
    raw(made, RAW, "---\nsource: claude-code\n---\n\n# branch · 2026-07-20\n\nAnd more.\n")

    assert lint.check(made) == []
    assert [item.status for item in wiki.pending(made)] == ["stale"]


def test_nothing_under_raw_or_the_schema_is_written(made):
    """It reports; it never repairs. Pinned on the two files most tempting to fix in passing."""
    page(made, "concept", "Session Parser")
    before = {
        path: path.read_bytes() for path in (made / "index.md", made / "CLAUDE.md", made / RAW)
    }

    assert lint.check(made)  # there is something to fix

    assert {path: path.read_bytes() for path in before} == before


# --------------------------------------------------------------------------------------
# the CLI surface
# --------------------------------------------------------------------------------------


def test_the_cli_prints_nothing_at_all_for_a_clean_vault(made):
    result = runner.invoke(app, ["lint", "--vault", str(made)])

    assert result.exit_code == 0
    assert result.output == ""


def test_the_cli_exits_zero_even_with_findings(made):
    """Findings are the normal state of a working vault, so this is a report and not a gate."""
    page(made, "concept", "Session Parser")

    result = runner.invoke(app, ["lint", "--vault", str(made)])

    assert result.exit_code == 0
    assert result.output


def test_each_finding_is_one_tab_separated_line(made):
    page(made, "source", "A Session", "\nBuilt on the [[Card Model]].\n", source=RAW)
    settle(made)

    result = runner.invoke(app, ["lint", "--vault", str(made)])

    assert result.output == "wiki/sources/A Session.md\tdead-link\t[[Card Model]]\n"


def test_findings_come_out_sorted_by_path_then_check(made):
    page(made, "concept", "Session Parser", "\nSee [[Card Model]].\n")
    page(made, "source", "A Session", "\nSee [[Card Model]].\n", source=RAW)
    settle(made)

    assert [item[0] for item in findings(made)] == [
        "wiki/concepts/Session Parser.md",
        "wiki/concepts/Session Parser.md",
        "wiki/sources/A Session.md",
    ]
    assert [item[1] for item in findings(made)][:2] == ["dead-link", "orphan"]


def test_the_cli_refuses_something_that_is_not_a_vault(tmp_path):
    result = runner.invoke(app, ["lint", "--vault", str(tmp_path)])

    assert result.exit_code == 1
    assert "not a crate vault" in result.output
