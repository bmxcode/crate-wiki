"""The deterministic half of `/lint`: what's wrong with a vault that has a single right answer.

Five checks, and the boundary between them and the four questions in `.claude/commands/lint.md`
is ADR-0008's, applied per step — see docs/adr/0020-the-linter-reports-and-never-repairs.md for
this operation's table. Whether `[[Card Model]]` resolves is a lookup; whether two pages
contradict each other is judgment, and stays with the model.

Two properties everything here holds to:

- **It reports; it never repairs.** Nothing in this module opens a file for writing. A tool that
  edits pages while claiming to check them is one you can't read the output of, and the fix for
  every finding here belongs to an operation that already owns it.
- **It never cries wolf.** A checker that fires on the vault the engine itself ships is one
  nobody keeps running, so the shipped templates are part of the test suite (issue #9). That is
  what `strip_code` is for, and why `sources/` and `daily/` pages are not orphan candidates.

Staleness is deliberately absent. A raw file that changed after the page written from it is
ordinary (ADR-0016) and `crate pending` already reports it, against the digest ledger recorded on
the page (ADR-0017). A second answer to that question would be one without the ledger.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from crate_wiki.wiki import (
    _FENCE,
    index_header,
    load_pages,
    normalise_source,
    private_sections,
    render_index,
)

# Page kinds a wikilink is supposed to reach. `sources/` is reached by ledger and `daily/` by
# date — an early vault is made almost entirely of those two, and reporting every one of them on
# day one is the same cry-wolf failure the code-span rule exists to prevent.
ORPHAN_KINDS = ("entities", "concepts", "syntheses")

# `[[Target]]`, `[[Target|alias]]`, `[[Target#Heading]]`. Newlines are excluded because a
# wikilink never spans one, and a nested `[` would mean this isn't one.
_WIKILINK = re.compile(r"\[\[([^\[\]\n]+)\]\]")

_BACKTICKS = re.compile(r"`+")


@dataclass(frozen=True)
class Finding:
    """One thing wrong with the vault, as one output line."""

    path: str  # vault-relative, posix — the file the finding is about
    check: str  # dead-link | index-stale | missing-source | orphan | private-source
    detail: str


# --------------------------------------------------------------------------------------
# reading markdown without reading its code
# --------------------------------------------------------------------------------------


def strip_code(text: str) -> str:
    """`text` with every fenced block and backtick code span blanked out, line structure intact.

    The gap `wiki.py` left. `reflow`'s `_FENCE` already knows what a fenced block is, and nothing
    anywhere knew what a code *span* is — which is the one that matters, because the `index.md`
    this engine ships says a page is catalogued as `` `[[Page Name]] — one line` `` and the vault
    schema illustrates linking with `` `the [[Session Parser]] drops tool output` ``. Obsidian
    doesn't linkify either, so neither is a dead link, and a naive sweep reports both on every
    vault forever.

    Indented code is deliberately not treated as code, for the reason `_is_structural` gives:
    inside a chunk, four spaces means a list continuation, and blanking those would silently drop
    real links out of every nested list.
    """
    lines = text.split("\n")
    out: list[str] = []
    fence: str | None = None

    for line in lines:
        if fence is not None:
            out.append("")
            if line.strip().startswith(fence):
                fence = None
            continue

        opening = _FENCE.match(line)
        if opening:
            fence = opening.group(1)
            out.append("")
            continue

        out.append(_blank_spans(line))

    return "\n".join(out)


def _blank_spans(line: str) -> str:
    """One line with its code spans replaced by spaces.

    CommonMark's rule: a run of N backticks opens a span, and the next run of *exactly* N closes
    it. A run with no matching closer is literal text, so scanning resumes after it rather than
    giving up on the line — `` ``unclosed and `a span` `` still has one span in it.

    Per line rather than across lines, which is safe here because `crate fmt` puts every paragraph
    on one line, so a span never straddles a newline in a vault this engine maintains.
    """
    out = list(line)
    position = 0

    while True:
        opener = _BACKTICKS.search(line, position)
        if opener is None:
            return "".join(out)

        run = opener.end() - opener.start()
        closer = _matching_run(line, opener.end(), run)
        if closer is None:
            position = opener.end()
            continue

        for index in range(opener.start(), closer):
            out[index] = " "
        position = closer


def _matching_run(line: str, start: int, run: int) -> int | None:
    """Where the next backtick run of exactly `run` ends, searching from `start`. None if absent."""
    while True:
        candidate = _BACKTICKS.search(line, start)
        if candidate is None:
            return None
        if candidate.end() - candidate.start() == run:
            return candidate.end()
        start = candidate.end()


def wikilinks(text: str) -> list[str]:
    """The page titles `text` links to, in order, with code skipped and the forms resolved.

    Obsidian's alias (`[[Page|shown text]]`) and heading (`[[Page#Section]]`) forms both resolve
    to `Page`, and `[[#Section]]` is a link within the same page and names no target at all.
    Neither is used in a vault yet, but nothing in the schema forbids them and a checker that read
    the whole inside of the brackets would call all three dead.
    """
    targets: list[str] = []
    for match in _WIKILINK.finditer(strip_code(text)):
        target = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
        if target:
            targets.append(target)
    return targets


# --------------------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------------------


def check(vault: Path) -> list[Finding]:
    """Every finding about `vault`, sorted. Empty means nothing mechanical is wrong with it."""
    root = vault.expanduser().resolve()
    private = private_sections(root)  # raises VaultError when this isn't a vault
    pages = load_pages(root)
    titles = {page.title for page in pages}

    findings: list[Finding] = []
    linked: set[str] = set()

    for page in pages:
        relative = page.path.relative_to(root).as_posix()
        for target in wikilinks(_read(page.path)):
            if target not in titles:
                findings.append(Finding(relative, "dead-link", f"[[{target}]]"))
            elif target != page.title:
                # A page linking to itself doesn't make it reachable from anywhere.
                linked.add(target)
        findings += _source_findings(root, page.sources, relative, private)

    findings += _index_findings(root, titles)

    for page in pages:
        if page.kind in ORPHAN_KINDS and page.title not in linked:
            relative = page.path.relative_to(root).as_posix()
            findings.append(Finding(relative, "orphan", "no other page links to it"))

    return sorted(findings, key=lambda finding: (finding.path, finding.check, finding.detail))


def _index_findings(vault: Path, titles: set[str]) -> list[Finding]:
    """Whether `index.md` still matches the pages on disk, and whether its own prose links resolve.

    The membership half is a **comparison, not a check**: `index.md` is derived (ADR-0008), so
    "which entries are missing" is `render_index` against the file, and the answer to any number
    of them is the same single `crate index`. Reporting one line per missing page would be a list
    you clear with one command, which is noise wearing information's clothes. `crate lint` says it
    once and doesn't fix it — regenerating here would make the linter a writer.

    Only the *header* is swept for links, because the header is the half a regeneration preserves
    and therefore the only half anyone authored. A dead link below it is a page that has gone
    away, which is the staleness finding again under another name.
    """
    findings: list[Finding] = []

    for target in wikilinks(index_header(vault)):
        if target not in titles:
            findings.append(Finding("index.md", "dead-link", f"[[{target}]]"))

    expected = render_index(vault, index_header(vault))
    if _read(vault / "index.md") != expected:
        findings.append(Finding("index.md", "index-stale", "out of date — run `crate index`"))

    return findings


def _source_findings(
    vault: Path, sources: tuple[str, ...], relative: str, private: set[str]
) -> list[Finding]:
    """What a page's `sources:` claims it was built from, checked against `raw/`.

    Two questions, and `crate pending` can answer neither. **A private source is the ADR-0006
    leak**, which that record asks the linter for by name: `public_sections` filters private
    sections out before `pending` walks anything, so a page citing one is invisible there by
    construction. **A missing source is the ledger's own dead link**: `pending` iterates the raw
    files that exist, so a page citing a deleted or mistyped path is silent everywhere today.

    Every page kind is checked, not just `wiki/sources/`. A daily page's `sources:` is raw card
    paths too (ADR-0012), so it can cite a private section exactly the same way. An entry that
    isn't a raw path is a `[[wikilink]]` — a synthesis's provenance — and it's already been
    checked as a link.
    """
    findings: list[Finding] = []

    for entry in sources:
        cited = normalise_source(entry)
        if not cited.startswith("raw/"):
            continue

        parts = cited.split("/")
        section = parts[1] if len(parts) > 1 else ""
        if section in private:
            # The path, never the content: this says a private section reached wiki/, and
            # repeating what's in it here would be the leak the check exists to report.
            findings.append(Finding(relative, "private-source", cited))
        elif not (vault / cited).is_file():
            findings.append(Finding(relative, "missing-source", cited))

    return findings


def _read(path: Path) -> str:
    """A file's text, or `""` if it can't be read. A linter degrades rather than raising."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
