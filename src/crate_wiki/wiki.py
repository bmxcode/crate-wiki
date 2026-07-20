"""Mechanics over an existing vault: what's unread, scaffolding a page, the index, the log.

These are the four steps of `/ingest` that have a single right answer, so they're code rather
than prompt — see docs/adr/0008-code-and-prompt-inside-an-operation.md. The steps between them
(what mattered, which page a fact belongs on, the prose) stay with the model.

Two properties everything here holds to:

- **Never drop a page.** The index groups by directory, not by the `type:` a page claims, so a
  page with broken frontmatter is still listed. Being wrong in the index is recoverable; being
  absent from it is the failure the index exists to prevent.
- **Read the vault's templates, not the package's.** `.crate/templates/` is copied into the
  vault so it can be customised there; scaffolding from the package would silently ignore that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from crate_wiki.vault import PAGE_TYPES, WIKI_DIRS, VaultError, load_config, template_text

# PAGE_TYPES and WIKI_DIRS are parallel by construction in vault.py — `source` lives in
# `sources/`. Deriving the mapping keeps one of them from drifting past the other.
DIR_FOR_TYPE = dict(zip(PAGE_TYPES, WIKI_DIRS, strict=True))

# Characters that would break the filename == H1 == [[wikilink]] invariant the link graph
# rests on, or escape the wiki directory entirely.
FORBIDDEN_IN_TITLE = set('/\\[]|#^:*?"<>')

SECTION_HEADINGS = {
    "sources": "Sources",
    "entities": "Entities",
    "concepts": "Concepts",
    "syntheses": "Syntheses",
    "daily": "Daily",
}

NO_SUMMARY = "*(no summary — add one to this page's frontmatter)*"


@dataclass(frozen=True)
class Page:
    """One file under `wiki/`. `kind` comes from the directory, which is always present."""

    path: Path
    title: str
    kind: str
    summary: str
    sources: tuple[str, ...]
    updated: str


@dataclass(frozen=True)
class Pending:
    """A raw file and where the wiki stands on it."""

    path: str  # vault-relative, posix
    status: str  # new | stale (ingested, but the raw file changed since) | ingested

    @property
    def needs_work(self) -> bool:
        return self.status != "ingested"


# --------------------------------------------------------------------------------------
# frontmatter
# --------------------------------------------------------------------------------------


def read_frontmatter(text: str) -> dict[str, str]:
    """The `key: value` block between the leading `---` fences, as raw strings.

    Deliberately not a YAML parser: the frontmatter shape is ours, it's flat, and every value
    is either a scalar or a one-line list. A page with no frontmatter yields `{}` rather than
    raising — it still has to appear in the index.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith((" ", "\t", "#")):
            fields[key.strip()] = value.strip()
    return fields


def parse_list(value: str) -> tuple[str, ...]:
    """`["a", "b"]` -> `("a", "b")`. Tolerates a bare scalar and an empty list."""
    value = value.strip()
    if not value:
        return ()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    items = [item.strip().strip("\"'") for item in value.split(",")]
    return tuple(item for item in items if item)


# --------------------------------------------------------------------------------------
# reading the wiki
# --------------------------------------------------------------------------------------


def load_pages(vault: Path) -> list[Page]:
    """Every page under `wiki/`, in index order: by section, then by title."""
    pages: list[Page] = []
    for directory in WIKI_DIRS:
        root = vault / "wiki" / directory
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.md")):
            try:
                fields = read_frontmatter(path.read_text(encoding="utf-8"))
            except OSError:
                fields = {}
            pages.append(
                Page(
                    path=path,
                    title=path.stem,
                    kind=directory,
                    summary=fields.get("summary", ""),
                    sources=parse_list(fields.get("sources", "")),
                    updated=fields.get("updated", ""),
                )
            )
    return pages


def _public_sections(vault: Path) -> list[str]:
    """Section names under `raw/` that may be synthesized into the wiki.

    Private sections are excluded outright (ADR-0006). The gitignore keeps them off a remote;
    this keeps them out of `wiki/`, which is the half a gitignore can't do.
    """
    config = load_config(vault)
    sections = config.get("raw", {}).get("sections", [])
    return [
        str(section["name"])
        for section in sections
        if isinstance(section, dict) and section.get("name") and not section.get("private")
    ]


def _raw_files(vault: Path, sections: list[str]) -> list[str]:
    """Every candidate source file under the given sections, vault-relative and sorted."""
    found: list[str] = []
    for name in sections:
        root = vault / "raw" / name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.name.startswith("."):
                continue
            found.append(path.relative_to(vault).as_posix())
    return sorted(found)


def _normalise(reference: str) -> str:
    """A `sources:` entry as a vault-relative posix path, for comparison against raw files.

    Entries that are wikilinks rather than paths (`[[Some Page]]`, which is what the non-source
    page types carry) normalise to something that matches no raw file, which is correct.
    """
    return reference.strip().removeprefix("./").strip("/")


def ingested(vault: Path) -> dict[str, Page]:
    """Raw paths already folded into the wiki, mapped to the source page that claims each.

    The ledger is the `sources:` frontmatter on `wiki/sources/` pages — not `.crate/state.json`,
    which is gitignored and therefore machine-local, and not `log.md`, which is prose. Reading it
    off the pages means it's committed, travels with the vault, and self-heals: delete a source
    page and its raw file is pending again, which is exactly right.
    """
    claims: dict[str, Page] = {}
    for page in load_pages(vault):
        if page.kind != "sources":
            continue
        for reference in page.sources:
            claims.setdefault(_normalise(reference), page)
    return claims


def pending(vault: Path, *, include_all: bool = False) -> list[Pending]:
    """Raw sources not yet ingested, plus any whose raw file has outrun its page."""
    claims = ingested(vault)
    results: list[Pending] = []

    for relative in _raw_files(vault, _public_sections(vault)):
        page = claims.get(relative)
        if page is None:
            status = "new"
        elif _is_stale(vault / relative, page):
            status = "stale"
        else:
            status = "ingested"

        if include_all or status != "ingested":
            results.append(Pending(relative, status))
    return results


def _is_stale(raw: Path, page: Page) -> bool:
    """Whether `raw` changed after the page that summarises it was last updated.

    A session that resumes gets its card rewritten in place, so an ingested source can grow new
    content. Comparing the raw file's mtime against the page's `updated:` date catches that. The
    comparison is date-granular, so same-day changes don't show up — the honest limitation of
    dating pages by day rather than timestamp.
    """
    if not page.updated:
        return False
    try:
        touched = date.fromtimestamp(raw.stat().st_mtime).isoformat()
    except (OSError, ValueError, OverflowError):
        return False
    return touched > page.updated


# --------------------------------------------------------------------------------------
# scaffolding a page
# --------------------------------------------------------------------------------------


def new_page(
    vault: Path,
    page_type: str,
    title: str,
    *,
    raw: str | None = None,
    today: str | None = None,
) -> Path:
    """Scaffold `title` as a `page_type` page from the vault's template. Returns the path.

    Refuses to overwrite an existing page: extending one is an edit, and silently replacing a
    page is the one unrecoverable move in a wiki whose raw layer is otherwise immutable.
    """
    if page_type not in DIR_FOR_TYPE:
        known = ", ".join(PAGE_TYPES)
        raise VaultError(f"unknown page type {page_type!r} — expected one of: {known}")

    title = title.strip()
    if not title:
        raise VaultError("a page needs a title — the title is the filename and the H1")
    bad = sorted(FORBIDDEN_IN_TITLE & set(title))
    if bad:
        raise VaultError(
            f"title {title!r} contains {' '.join(bad)} — a title is also a filename and a "
            "[[wikilink]], so it can't hold those"
        )

    if page_type == "source" and not raw:
        raise VaultError(
            "a source page needs --raw: the raw path it records is what makes re-running "
            "/ingest skip this source instead of duplicating it"
        )

    template = vault / ".crate" / "templates" / f"{page_type}.md"
    if not template.is_file():
        raise VaultError(f"no template at {template} — run `crate upgrade` on this vault")

    path = vault / "wiki" / DIR_FOR_TYPE[page_type] / f"{title}.md"
    if path.exists():
        raise VaultError(f"{path} already exists — extend it rather than replacing it")

    stamp = today or date.today().isoformat()
    text = _fill(template.read_text(encoding="utf-8"), title, raw, stamp)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _fill(template: str, title: str, raw: str | None, today: str) -> str:
    """Stamp the title, today's date and (for a source) the raw path into a template.

    The templates carry literal `YYYY-MM-DD` and a placeholder H1 rather than `$` substitutions,
    so they stay readable as skeletons in Obsidian. Dates are only replaced inside the
    frontmatter — a body that discusses a date shouldn't be rewritten.
    """
    lines = template.splitlines()
    in_frontmatter = bool(lines) and lines[0].strip() == "---"
    h1_done = False
    out: list[str] = []

    for index, line in enumerate(lines):
        if in_frontmatter and index > 0 and line.strip() == "---":
            in_frontmatter = False
            out.append(line)
            continue

        if in_frontmatter:
            if raw and line.startswith("sources:"):
                line = f'sources: ["{raw}"]'
            else:
                line = line.replace("YYYY-MM-DD", today)
        elif not h1_done and line.startswith("# "):
            line = f"# {title}"
            h1_done = True

        out.append(line)

    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------------------
# index.md
# --------------------------------------------------------------------------------------


def render_index(vault: Path, header: str) -> str:
    """`index.md` as a function of the pages on disk. Nothing here is a judgment call."""
    lines = [header.rstrip(), ""]
    pages = load_pages(vault)

    for directory in WIKI_DIRS:
        lines += [f"## {SECTION_HEADINGS[directory]}", ""]
        section = [page for page in pages if page.kind == directory]
        for page in section:
            summary = page.summary.strip() or NO_SUMMARY
            lines.append(f"- [[{page.title}]] — {summary}")
        if not section:
            lines.append("*Nothing here yet.*")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def index_header(vault: Path) -> str:
    """The hand-written prose above the generated sections, preserved across regenerations.

    Taken from the existing `index.md` up to the first `## ` heading, so anything the user adds
    at the top of the file survives. A missing or heading-less index falls back to the template.
    """
    path = vault / "index.md"
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        head = text.split("\n## ", 1)[0].rstrip()
        if head:
            return head
    return template_text("vault", "index.md").rstrip()


def reindex(vault: Path) -> Path:
    """Regenerate `index.md` from page frontmatter. Returns the path."""
    if not (vault / "wiki").is_dir():
        raise VaultError(f"no wiki/ directory in {vault} — is this a vault?")
    path = vault / "index.md"
    path.write_text(render_index(vault, index_header(vault)), encoding="utf-8")
    return path


# --------------------------------------------------------------------------------------
# log.md
# --------------------------------------------------------------------------------------


def append_log(vault: Path, operation: str, title: str, *, today: str | None = None) -> str:
    """Append one `## [YYYY-MM-DD] op | Title` entry. Returns the line written.

    Append-only by construction: this reads the file only to decide how many newlines it needs,
    and never rewrites a byte of what's already there.
    """
    path = vault / "log.md"
    if not path.is_file():
        raise VaultError(f"no log.md in {vault} — is this a vault?")

    title = " ".join(title.split())  # a newline here would forge a second entry
    if not title:
        raise VaultError("a log entry needs a title")

    line = f"## [{today or date.today().isoformat()}] {operation} | {title}"
    existing = path.read_text(encoding="utf-8")
    separator = "" if existing.endswith("\n\n") else "\n" if existing.endswith("\n") else "\n\n"

    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{separator}{line}\n")
    return line
