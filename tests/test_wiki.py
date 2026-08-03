"""Tests for the mechanical half of the operations: pending, day, new, extend, index, fmt, log.

Fixtures are synthetic and built in tmp_path — no real vault content reaches this repo.
"""

import os
from datetime import date, datetime, timedelta

import pytest
from typer.testing import CliRunner

from crate_wiki import vault, wiki
from crate_wiki.cli import app

runner = CliRunner()

# Staleness compares a raw file's *content* against the digest its page recorded (#22), and falls
# back to comparing mtime against `updated:` for pages written before `source_hash:` existed. The
# fallback is why these fixtures still pin both to a fixed day rather than writing the raw file
# *now* and dating the page in the past: that shape goes stale the moment the real clock passes
# the date. These tests are about the ledger, not about what day it happens to be when they run.
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


def rewrite(target, text, day=TODAY):
    """Change the raw card's *content*, keeping its mtime on `day`. What a resume does."""
    raw = target / RAW
    raw.write_text(text, encoding="utf-8")
    touch(raw, day)


def test_a_raw_file_rewritten_after_ingest_is_stale(made):
    """A resumed session rewrites its card, so an ingested source can outrun the page about it."""
    source_page(made)
    rewrite(
        made, "---\nsource: claude-code\n---\n\n# branch · 2026-07-20\n\nAnd more.\n", "2026-07-24"
    )

    assert [item.status for item in wiki.pending(made)] == ["stale"]


def test_a_same_day_rewrite_is_stale(made):
    # The bug (#22). The Stop hook rewrites a card continuously while its session runs, so the
    # rewrite almost always lands on the day the page was ingested — and a day-granular
    # comparison can never see it, because "2026-07-20" > "2026-07-20" is false.
    source_page(made)
    rewrite(made, "---\nsource: claude-code\n---\n\n# branch · 2026-07-20\n\nSecond half.\n")

    assert [item.status for item in wiki.pending(made)] == ["stale"]


def test_a_source_that_only_changed_mtime_is_not_stale(made):
    # A fresh `git clone` gives every file the checkout time, which is newer than every page's
    # `updated:` — so the old mtime comparison reported an entire vault as stale on a machine
    # where nothing was wrong. ADR-0010 rejected mtime for `.crate/baseline.json` on exactly this
    # reasoning; the ingest ledger just never got the same treatment.
    source_page(made)
    touch(made / RAW, "2027-01-01")

    assert [item.status for item in wiki.pending(made, include_all=True)] == ["ingested"]


def test_a_source_that_lost_content_is_stale(made):
    # After ADR-0016 a rewind can make a card *shrink*, so an ingested page may describe work the
    # source no longer holds. Staleness has to catch a source moving in either direction.
    source_page(made)
    rewrite(made, "---\nsource: claude-code\n---\n")

    assert [item.status for item in wiki.pending(made)] == ["stale"]


def test_a_page_with_no_recorded_hash_falls_back_to_the_mtime_check(made):
    # Every page in every vault predating `source_hash:` is in this state. The fallback keeps
    # their behaviour exactly as it was rather than silently reporting them all fresh, and the
    # page upgrades itself the next time `crate extend --source` touches it.
    page = source_page(made)
    strip_source_hash(page)
    touch(made / RAW, "2026-07-24")

    assert [item.status for item in wiki.pending(made)] == ["stale"]


def strip_source_hash(page):
    """Drop the `source_hash:` line, leaving a page the way a pre-D21 vault wrote it."""
    kept = [
        line
        for line in page.read_text(encoding="utf-8").splitlines()
        if not line.startswith("source_hash:")
    ]
    page.write_text("\n".join(kept) + "\n", encoding="utf-8")


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
# pending — the running session's own card
# --------------------------------------------------------------------------------------
#
# A card of the session you're in is still being written, so `/ingest` can't usefully fold it in
# — see wiki._live_card. Every filename here is built from `date.today()` and a synthetic id:
# never a literal date, so CRATE_TEST_CLOCK passes too, and never a real session id, because this
# repo's history is exposed retroactively.

LIVE_ID = "11111111-2222-3333-4444-555555555555"
OTHER_ID = "99999999-8888-7777-6666-555555555555"


@pytest.fixture(autouse=True)
def no_ambient_session(monkeypatch):
    """Unset the live-session signal for every test in this module.

    The suite runs inside Claude Code as often as in CI, where the variable is genuinely set —
    and a test that behaves differently depending on what launched it is the environment-dependent
    flake `pinned_tz` and `CRATE_TEST_CLOCK` both exist for. Tests that want the signal set it.
    """
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


def session_card(target, session_id, *, day=None):
    """A synthetic card, named the way the capture layer names one. Returns its relative path."""
    day = day or date.today().isoformat()
    relative = f"raw/sessions/claude-code/{day}-{session_id}.md"
    path = target / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nsource: claude-code\n---\n\n# branch · {day}\n", encoding="utf-8")
    touch(path, TODAY)
    return relative


def running(monkeypatch, session_id=LIVE_ID):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", session_id)


def test_the_running_sessions_card_is_live(made, monkeypatch):
    relative = session_card(made, LIVE_ID)
    running(monkeypatch)

    assert (relative, "live") in [(item.path, item.status) for item in wiki.pending(made)]


def test_another_sessions_card_is_untouched(made, monkeypatch):
    """Only the *current* session is detectable; a different session's card is just a source."""
    relative = session_card(made, OTHER_ID)
    running(monkeypatch)

    assert (relative, "new") in [(item.path, item.status) for item in wiki.pending(made)]


def test_an_earlier_days_card_of_the_running_session_is_not_live(made, monkeypatch):
    # The day-split decision (ADR-0015/0016). One session yields one card per day it was active
    # on, all sharing an id — so matching the id alone would mark finished days too, and a day of
    # complete work that `/ingest` never offers is worse than the problem this fixes.
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    relative = session_card(made, LIVE_ID, day=yesterday)
    running(monkeypatch)

    assert (relative, "new") in [(item.path, item.status) for item in wiki.pending(made)]


def test_live_wins_over_new(made, monkeypatch):
    relative = session_card(made, LIVE_ID)
    running(monkeypatch)

    assert [item.status for item in wiki.pending(made) if item.path == relative] == ["live"]


def test_live_wins_over_stale(made, monkeypatch):
    """A live card is *always* stale — the Stop hook rewrites it every turn — and saying so is
    noise, because you can't act on it until the session ends."""
    relative = session_card(made, LIVE_ID)
    source_page(made, title="Live Session", raw=relative)
    (made / relative).write_text("---\nsource: claude-code\n---\n\nMore.\n", encoding="utf-8")
    running(monkeypatch)

    assert [item.status for item in wiki.pending(made) if item.path == relative] == ["live"]


def test_an_ingested_card_is_not_resurfaced_by_being_live(made, monkeypatch):
    """`live` relabels a line that would print anyway; it never adds one nobody can act on."""
    relative = session_card(made, LIVE_ID)
    source_page(made, title="Live Session", raw=relative)
    running(monkeypatch)

    assert [item.status for item in wiki.pending(made) if item.path == relative] == []


def test_nothing_is_marked_when_the_signal_is_unset(made):
    # Fail open: a plain shell, a Codex sweep, a future release that drops the variable. The check
    # must never be the reason a card goes un-ingested.
    session_card(made, LIVE_ID)
    assert {item.status for item in wiki.pending(made)} == {"new"}


def test_nothing_is_marked_when_the_signal_matches_no_card(made, monkeypatch):
    session_card(made, LIVE_ID)
    running(monkeypatch, "not-a-session-that-has-a-card")

    assert {item.status for item in wiki.pending(made)} == {"new"}


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


def digests(target, title="Fake Session"):
    """A page's `source_hash:` as `raw path -> digest`, read the way `pending` reads it."""
    page = next(item for item in wiki.load_pages(target) if item.title == title)
    return wiki.recorded_digests(page)


def add_later_source(target, relative="raw/sessions/claude-code/later.md"):
    """A second raw card, cited by the same page — the multi-source shape."""
    (target / relative).write_text("---\nsource: claude-code\n---\n\n# later\n", encoding="utf-8")
    touch(target / relative, TODAY)
    wiki.extend_page(target, "Fake Session", source=relative, today=TODAY)
    return relative


def test_scaffolding_a_source_page_records_the_digest_it_read(made):
    # Scaffolding with --raw already puts that path in the ingest ledger, so the state it was
    # read in has to be recorded at the same moment or the page starts life unable to notice.
    source_page(made)

    assert digests(made) == {RAW: wiki.source_digest(made / RAW)}


def test_extending_records_the_digest_of_the_source_it_absorbed(made):
    source_page(made)
    later = add_later_source(made)

    assert digests(made) == {
        RAW: wiki.source_digest(made / RAW),
        later: wiki.source_digest(made / later),
    }


def test_only_the_source_that_changed_is_stale(made):
    # A page can cite several raw files. The record is per source and self-describing, so one
    # card moving on doesn't drag its neighbours into `stale` alongside it.
    source_page(made)
    add_later_source(made)
    assert wiki.pending(made) == []

    rewrite(made, "---\nsource: claude-code\n---\n\n# branch\n\nResumed.\n")

    assert [(item.path, item.status) for item in wiki.pending(made)] == [(RAW, "stale")]


def test_a_reordered_ledger_still_resolves_each_digest(made):
    # The entries name their own path rather than sitting positionally alongside `sources:`, so
    # hand-editing or reordering either list can't silently pair a path with another file's hash.
    page = source_page(made)
    add_later_source(made)
    reverse_source_hash(page)

    assert wiki.pending(made) == []


def reverse_source_hash(page):
    """Flip the order of the page's `source_hash:` entries, leaving `sources:` alone."""
    lines = page.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("source_hash:"):
            entries = reversed(wiki.parse_list(line.partition(":")[2]))
            joined = ", ".join(f'"{entry}"' for entry in entries)
            lines[index] = f"source_hash: [{joined}]"
    page.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_re_extending_a_stale_source_records_its_new_state(made):
    # Absorbing the rest of a resumed session is what clears `stale` — the recorded hash has to
    # move forward with it, or the page stays permanently stale against work it has just read.
    source_page(made)
    rewrite(made, "---\nsource: claude-code\n---\n\n# branch\n\nSecond half.\n")
    assert [item.status for item in wiki.pending(made)] == ["stale"]

    wiki.extend_page(made, "Fake Session", source=RAW, today=TODAY)

    assert wiki.pending(made) == []


def test_a_wikilink_source_gets_no_digest_entry(made):
    # A synthesis cites [[Page]], not a path. There's no file behind it to hash, and inventing
    # an entry would put something in the ledger that can never resolve.
    wiki.new_page(made, "concept", "Session Parser", today=TODAY)

    path, _ = wiki.extend_page(made, "Session Parser", source="[[A]]", today="2026-07-24")

    assert "source_hash" not in wiki.read_frontmatter(path.read_text(encoding="utf-8"))


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
# day — which session cards belong to a date, and in what order (ADR-0012)
# --------------------------------------------------------------------------------------


def card(target, name, started=None, front_end="claude-code"):
    """A synthetic session card: the frontmatter `crate day` reads, and nothing else.

    `started=None` writes a card with no `started:` field, which is the shape `day_cards` has to
    fall back on the filename for.
    """
    path = target / "raw" / "sessions" / front_end / name
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = f"started: {started}\n" if started else ""
    path.write_text(f"---\nsource: claude-code\n{stamp}---\n\n# branch\n", encoding="utf-8")
    return path


def test_a_day_holds_the_cards_that_started_on_it_and_no_others(made):
    card(made, "2026-07-21-11111111.md", "2026-07-21T09:00:00Z")
    card(made, "2026-07-22-22222222.md", "2026-07-22T09:00:00Z")

    assert wiki.day_cards(made, "2026-07-21") == ["raw/sessions/claude-code/2026-07-21-11111111.md"]


def test_a_day_is_ordered_by_when_each_session_started_not_by_filename(made):
    """The reason this is code: a card is `<date>-<short id>.md`, so names sort by session id."""
    card(made, "2026-07-21-aaaaaaaa.md", "2026-07-21T16:00:00Z")
    card(made, "2026-07-21-bbbbbbbb.md", "2026-07-21T09:00:00Z")

    assert wiki.day_cards(made, "2026-07-21") == [
        "raw/sessions/claude-code/2026-07-21-bbbbbbbb.md",
        "raw/sessions/claude-code/2026-07-21-aaaaaaaa.md",
    ]


def test_a_day_is_ordered_by_instant_not_by_string_across_an_offset_change(made):
    """`started:` is now local, so it carries a UTC offset that can vary across a DST boundary
    (#29). Lexicographic string sort gets this pair backwards: "01:15" text-sorts before "01:30"
    even though the +11:00 card's instant (14:30 UTC) is earlier than the +10:00 card's (15:15
    UTC) — an AEDT-then-AEST-style transition."""
    card(made, "2026-04-05-aaaaaaaa.md", "2026-04-05T01:30:00+11:00")  # 2026-04-04T14:30Z
    card(made, "2026-04-05-bbbbbbbb.md", "2026-04-05T01:15:00+10:00")  # 2026-04-04T15:15Z

    assert wiki.day_cards(made, "2026-04-05") == [
        "raw/sessions/claude-code/2026-04-05-aaaaaaaa.md",
        "raw/sessions/claude-code/2026-04-05-bbbbbbbb.md",
    ]


def test_a_card_without_a_started_field_still_belongs_to_the_day_in_its_name(made):
    """The `made` fixture's card carries no `started:` — it must not vanish from its own day."""
    assert wiki.day_cards(made, TODAY) == [RAW]


def test_a_day_spans_every_session_front_end(made):
    """A Codex card (D7) sits in the same day as a Claude Code one, with no change here."""
    card(made, "2026-07-21-cccccccc.md", "2026-07-21T10:00:00Z", front_end="codex")
    card(made, "2026-07-21-dddddddd.md", "2026-07-21T08:00:00Z")

    assert wiki.day_cards(made, "2026-07-21") == [
        "raw/sessions/claude-code/2026-07-21-dddddddd.md",
        "raw/sessions/codex/2026-07-21-cccccccc.md",
    ]


def test_a_days_cards_do_not_depend_on_mtime(made):
    """A `git checkout` rewrites every mtime, and a resumed session rewrites its card later."""
    touch(card(made, "2026-07-21-eeeeeeee.md", "2026-07-21T09:00:00Z"), "2026-07-29")

    assert wiki.day_cards(made, "2026-07-21") == ["raw/sessions/claude-code/2026-07-21-eeeeeeee.md"]
    assert wiki.day_cards(made, "2026-07-29") == []


def test_an_undated_file_under_sessions_belongs_to_no_day(made):
    card(made, "README.md")

    assert wiki.day_cards(made, TODAY) == [RAW]


def test_a_private_sessions_section_yields_no_cards(made):
    """ADR-0006: private sections are context only, and a daily page is `wiki/`."""
    config = made / ".crate" / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'name = "sessions"\nprivate = false', 'name = "sessions"\nprivate = true'
        ),
        encoding="utf-8",
    )

    assert wiki.day_cards(made, TODAY) == []


def test_an_already_ingested_card_still_belongs_to_its_day(made):
    """Why `crate pending` can't answer this: it hides ingested sources, and a day can't."""
    source_page(made)
    wiki.extend_page(made, "Fake Session", source=RAW, today=TODAY)

    assert [item.path for item in wiki.pending(made)] == []
    assert wiki.day_cards(made, TODAY) == [RAW]


def test_a_day_with_nothing_in_it_is_empty_rather_than_an_error(made):
    assert wiki.day_cards(made, "2026-01-01") == []


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        (None, "2026-07-19"),
        ("", "2026-07-19"),
        ("yesterday", "2026-07-19"),
        ("Yesterday", "2026-07-19"),
        ("today", "2026-07-20"),
        ("2026-03-04", "2026-03-04"),
    ],
)
def test_a_day_expression_resolves_to_one_date(expression, expected):
    assert wiki.resolve_day(expression, today=TODAY) == expected


def test_a_blank_day_is_yesterday_by_the_clock_the_engine_reads():
    """Asserted against `date.today()`, never a literal — so CRATE_TEST_CLOCK passes too."""
    assert wiki.resolve_day() == (date.today() - timedelta(days=1)).isoformat()


def test_a_day_that_isnt_a_date_says_what_it_accepts():
    with pytest.raises(vault.VaultError, match="YYYY-MM-DD"):
        wiki.resolve_day("last tuesday")


# --------------------------------------------------------------------------------------
# daily — the page mechanics /daily reuses, and the ledger it must not touch (ADR-0012)
# --------------------------------------------------------------------------------------


def test_a_daily_page_is_titled_by_the_day_it_covers(made):
    """The title is the day; `created:` is when the page was written, which is another day."""
    path = wiki.new_page(made, "daily", "2026-07-19", today=TODAY)
    fields = wiki.read_frontmatter(path.read_text(encoding="utf-8"))

    assert path == made / "wiki" / "daily" / "2026-07-19.md"
    assert "# 2026-07-19" in path.read_text(encoding="utf-8")
    assert fields["created"] == TODAY


def test_a_daily_page_records_the_raw_cards_it_was_written_from(made):
    """`sources:` on a daily page is raw paths — what it was built from, in the layer it read."""
    wiki.new_page(made, "daily", "2026-07-20", today=TODAY)
    wiki.extend_page(made, "2026-07-20", source=RAW, today=TODAY)

    page = made / "wiki" / "daily" / "2026-07-20.md"
    sources = wiki.parse_list(wiki.read_frontmatter(page.read_text(encoding="utf-8"))["sources"])
    assert sources == (RAW,)


def test_a_daily_page_citing_a_card_never_makes_it_look_ingested(made):
    """The ledger reads only wiki/sources/, so a daily page can cite raw without claiming it."""
    wiki.new_page(made, "daily", "2026-07-20", today=TODAY)
    wiki.extend_page(made, "2026-07-20", source=RAW, today=TODAY)

    assert wiki.ingested(made) == {}
    assert [item.path for item in wiki.pending(made)] == [RAW]


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


# `--sessions-dir` points every test here at an empty tmp dir rather than the real
# `~/.codex/sessions` `pending` defaults to — otherwise these tests' output would depend on
# whatever Codex sessions happen to exist on the machine running them.


def test_pending_prints_a_bare_path_for_a_new_source(made, tmp_path):
    result = runner.invoke(
        app, ["pending", "--vault", str(made), "--sessions-dir", str(tmp_path / "no-sessions")]
    )

    assert result.exit_code == 0
    assert result.output.strip() == "raw/sessions/claude-code/2026-07-20-abcd1234.md"


def test_pending_says_nothing_and_succeeds_when_there_is_nothing_to_do(made, tmp_path):
    source_page(made)
    result = runner.invoke(
        app, ["pending", "--vault", str(made), "--sessions-dir", str(tmp_path / "no-sessions")]
    )

    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_pending_nudges_when_codex_has_unswept_rollouts(made, tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "rollout-a.jsonl").write_text("{}\n", encoding="utf-8")

    result = runner.invoke(app, ["pending", "--vault", str(made), "--sessions-dir", str(sessions)])

    assert result.exit_code == 0
    assert "1 Codex rollouts not yet swept — run /fetch-codex" in result.output


def test_the_cli_reports_a_bad_vault_rather_than_a_traceback(tmp_path):
    result = runner.invoke(app, ["pending", "--vault", str(tmp_path)])

    assert result.exit_code == 1
    assert "not a crate vault" in result.output


def test_new_prints_the_path_it_wrote(made):
    result = runner.invoke(app, ["new", "concept", "Session Parser", "--vault", str(made)])

    assert result.exit_code == 0
    assert result.output.strip().endswith("Session Parser.md")


def test_day_prints_the_resolved_date_then_the_cards(made):
    card(made, "2026-07-21-aaaaaaaa.md", "2026-07-21T16:00:00Z")
    card(made, "2026-07-21-bbbbbbbb.md", "2026-07-21T09:00:00Z")
    result = runner.invoke(app, ["day", "2026-07-21", "--vault", str(made)])

    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "2026-07-21",
        "raw/sessions/claude-code/2026-07-21-bbbbbbbb.md",
        "raw/sessions/claude-code/2026-07-21-aaaaaaaa.md",
    ]


def test_day_still_prints_the_date_when_nothing_happened(made):
    """The date is the one thing the caller can't read off the paths when there are none."""
    result = runner.invoke(app, ["day", "2026-01-01", "--vault", str(made)])

    assert result.exit_code == 0
    assert result.output.splitlines() == ["2026-01-01"]


def test_day_reports_an_unreadable_date_rather_than_guessing(made):
    result = runner.invoke(app, ["day", "last tuesday", "--vault", str(made)])

    assert result.exit_code == 1
    assert "YYYY-MM-DD" in result.output


def test_log_through_the_cli_appends_one_entry(made):
    result = runner.invoke(app, ["log", "ingest", "--title", "Fake Session", "--vault", str(made)])

    assert result.exit_code == 0
    assert "] ingest | Fake Session" in (made / "log.md").read_text(encoding="utf-8")
