"""`crate add` — normalizing pastes and web clips into raw sources.

Fixtures are synthetic strings (CLAUDE.md: never a real capture). The one that matters most is
the round-trip: a source `crate add` writes must show up in `crate pending`, because being
ingestable is the whole point of writing it.
"""

import pytest
from typer.testing import CliRunner

from crate_wiki import intake, wiki
from crate_wiki.cli import app
from crate_wiki.vault import VaultError

# Reuse the Codex suite's vault builder, the way test_cli does.
from test_codex import make_vault

runner = CliRunner()


# A synthetic Obsidian Clipper capture: quoted scalars, the page URL under `source`, an article
# body below the block. Not a real clip — invented for this test.
CLIPPER = """\
---
title: "Deep Modules and Shallow Ones"
source: "https://example.com/deep-modules"
author: "A. Writer"
published: "2026-05-01"
tags:
  - design
---

A deep module hides a lot behind a small interface.

## Why it matters

The narrower the interface, the less a caller must know.
"""


# ------------------------------------------------------------------------------ slugify


def test_slugify_folds_accents_and_joins_words():
    assert intake.slugify("Résumé of the Deploy") == "resume-of-the-deploy"


def test_slugify_rejects_a_title_with_nothing_to_slug():
    with pytest.raises(VaultError):
        intake.slugify("!!! ???")


# ------------------------------------------------------------------------------ paste


def test_paste_keeps_the_text_verbatim_under_normalized_frontmatter():
    out = intake.normalize_paste(
        "hey can you look at the deploy?\nthx",
        title="Deploy ping",
        origin="slack",
        captured="2026-09-01",
    )
    fields = wiki.read_frontmatter(out)
    assert fields["source"] == "paste"
    assert fields["title"] == "Deploy ping"
    assert fields["origin"] == "slack"
    assert fields["captured"] == "2026-09-01"
    assert "# Deploy ping" in out
    assert "hey can you look at the deploy?\nthx" in out


def test_paste_omits_origin_when_none_was_given():
    out = intake.normalize_paste("some text", title="T", captured="2026-09-01")
    assert "origin:" not in out


def test_an_empty_paste_is_an_error():
    with pytest.raises(VaultError):
        intake.normalize_paste("   \n  ", title="T", captured="2026-09-01")


# ------------------------------------------------------------------------------ clip


def test_clip_reads_title_url_and_author_from_a_clipper_capture():
    out = intake.normalize_clip(CLIPPER, captured="2026-09-01")
    fields = wiki.read_frontmatter(out)
    assert fields["source"] == "clip"
    assert fields["title"] == "Deep Modules and Shallow Ones"  # quotes stripped
    assert fields["url"] == "https://example.com/deep-modules"
    assert fields["author"] == "A. Writer"
    assert fields["published"] == "2026-05-01"


def test_clip_strips_the_clipper_frontmatter_from_the_body():
    out = intake.normalize_clip(CLIPPER, captured="2026-09-01")
    body = out.split("---", 2)[2]
    assert "tags:" not in body  # the clipper block is gone
    assert "A deep module hides a lot" in body


def test_clip_overrides_win_over_the_capture():
    out = intake.normalize_clip(
        CLIPPER, url="https://override.example/x", title="My Title", captured="2026-09-01"
    )
    fields = wiki.read_frontmatter(out)
    assert fields["title"] == "My Title"
    assert fields["url"] == "https://override.example/x"


def test_plain_article_text_needs_an_explicit_title():
    with pytest.raises(VaultError):
        intake.normalize_clip("just some prose with no frontmatter", captured="2026-09-01")


# ------------------------------------------------------------------------------ write_source


def test_write_source_refuses_to_overwrite(tmp_path):
    target = make_vault(tmp_path)
    content = intake.normalize_paste("x", title="T", captured="2026-09-01")
    name = intake.source_filename("2026-09-01", "T")
    intake.write_source(target, "pastes", name, content)
    with pytest.raises(VaultError):
        intake.write_source(target, "pastes", name, content)


def test_write_source_refuses_an_unknown_section(tmp_path):
    target = make_vault(tmp_path)
    with pytest.raises(VaultError):
        intake.write_source(target, "nope", "x.md", "body")


def test_write_source_refuses_a_private_section(tmp_path):
    # A personal vault's `journal` is private (ADR-0006): a source there could never be ingested.
    target = make_vault(tmp_path)
    with pytest.raises(VaultError):
        intake.write_source(target, "journal", "x.md", "body")


# ------------------------------------------------------------------------------ CLI, end to end


def test_add_paste_then_pending_lists_it(tmp_path):
    target = make_vault(tmp_path)
    result = runner.invoke(
        app,
        [
            "add",
            "paste",
            "--title",
            "Deploy ping",
            "--from",
            "slack",
            "--date",
            "2026-09-01",
            "--vault",
            str(target),
        ],
        input="look at the deploy\nthx",
    )
    assert result.exit_code == 0, result.output
    written = result.output.strip()
    assert "raw/pastes/2026-09-01-deploy-ping.md" in written

    pending = [item.path for item in wiki.pending(target)]
    assert "raw/pastes/2026-09-01-deploy-ping.md" in pending


def test_add_url_from_a_clipper_file_then_pending_lists_it(tmp_path):
    target = make_vault(tmp_path)
    clip_file = tmp_path / "clip.md"
    clip_file.write_text(CLIPPER, encoding="utf-8")

    result = runner.invoke(
        app,
        ["add", "url", "--file", str(clip_file), "--date", "2026-09-01", "--vault", str(target)],
    )
    assert result.exit_code == 0, result.output
    assert "raw/clips/2026-09-01-deep-modules-and-shallow-ones.md" in result.output

    pending = [item.path for item in wiki.pending(target)]
    assert "raw/clips/2026-09-01-deep-modules-and-shallow-ones.md" in pending


def test_add_paste_with_no_input_fails(tmp_path):
    target = make_vault(tmp_path)
    # No stdin and no --file: strict, unlike a capture hook.
    result = runner.invoke(app, ["add", "paste", "--title", "T", "--vault", str(target)], input="")
    # CliRunner feeds "" as a non-tty stream, so this exercises the empty-paste guard.
    assert result.exit_code == 1
