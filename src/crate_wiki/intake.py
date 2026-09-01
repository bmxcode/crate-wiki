"""Turning non-session material into raw sources — the deterministic half of `crate add`.

Sessions capture themselves (cards.py, claude.py, codex.py). The *other* things worth keeping —
a pasted Slack thread, a web article you clipped — don't arrive as a session, so they get their
own free, deterministic intake here: normalize them into a frontmatter'd markdown file under a
`raw/` section, where `crate pending` finds them like any other source (wiki._raw_files).

**Normalize, never fetch.** This module reads content the user already has; it does no network
I/O and pulls in no new dependency. Obsidian Clipper already fetches URLs, and a paste is text in
hand. Keeping the engine offline is a stated value — see
docs/adr/0022-ingesters-normalize-not-fetch.md.

The frontmatter shape is shared across ingesters so a clip and a paste read the same way, and
parallels a session card's: it leads with `source:` (the ingester kind, as a card leads with
`source: claude-code`), then `title:`, `url:`, `captured:`, then any per-kind extras.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path

from crate_wiki import wiki
from crate_wiki.vault import VaultError, load_config

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    """A filename-safe slug: lowercase, ascii, words joined by single hyphens.

    Accents are folded rather than dropped (`Résumé` -> `resume`), so a title that's all
    non-ascii still yields something. An empty result raises — a source needs a filename, and a
    silent `untitled` would collide the moment a second one arrives.
    """
    folded = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("-", folded.lower()).strip("-")
    if not slug:
        raise VaultError(f"title {title!r} has no letters or digits to build a filename from")
    return slug


def render_source(
    source: str,
    title: str,
    *,
    url: str,
    captured: str,
    body: str,
    extra: dict[str, str] | None = None,
) -> str:
    """The shared frontmatter + body for every ingested raw source.

    `extra` is the per-kind tail (a paste's `origin:`, a clip's `author:`/`published:`), rendered
    in insertion order after the common keys so the common shape is always the same four lines.
    The body is written verbatim after the H1 — this is Layer 1, immutable ground truth, so it is
    never reflowed or edited.
    """
    lines = [
        "---",
        f"source: {source}",
        f"title: {title}",
        f"url: {url}",
        f"captured: {captured}",
    ]
    for key, value in (extra or {}).items():
        lines.append(f"{key}: {value}")
    lines += ["---", "", f"# {title}", "", body.strip("\n")]
    return "\n".join(lines) + "\n"


def normalize_paste(
    text: str,
    *,
    title: str,
    origin: str = "",
    url: str = "",
    captured: str | None = None,
) -> str:
    """A pasted message (Slack, email, Teams) as a raw source, its text kept verbatim.

    `origin` is the surface it came from; it's recorded but not required — where a paste came from
    is often all the provenance there is, and sometimes there isn't even that.
    """
    title = _require_title(title)
    body = text.strip("\n")
    if not body.strip():
        raise VaultError("a paste has no content — pass text on stdin or with --file")
    return render_source(
        "paste",
        title,
        url=url.strip(),
        captured=captured or date.today().isoformat(),
        body=body,
        extra={"origin": origin.strip()} if origin.strip() else None,
    )


def normalize_clip(
    content: str,
    *,
    url: str = "",
    title: str = "",
    captured: str | None = None,
) -> str:
    """A web clip as a raw source, reading an Obsidian Clipper capture when that's the input.

    Clipper writes a YAML frontmatter block (`title`, `source`, `author`, `published`, …) above
    the article markdown. When `content` carries one, its fields seed this source's frontmatter and
    the block itself is stripped from the body; explicit `url`/`title` override whatever it held.
    Plain article text with no frontmatter works too — you just have to pass `--title`/`--url`.
    """
    fields = wiki.read_frontmatter(content)
    body = _strip_frontmatter(content)

    # Clipper names the page URL `source`; fall back to `url` for hand-written frontmatter.
    resolved_url = (url or _scalar(fields, "source") or _scalar(fields, "url")).strip()
    resolved_title = _require_title(title or _scalar(fields, "title"))

    extra: dict[str, str] = {}
    for key in ("author", "published"):
        value = _scalar(fields, key)
        if value:
            extra[key] = value

    if not body.strip():
        raise VaultError("this clip has no content once its frontmatter is stripped")

    return render_source(
        "clip",
        resolved_title,
        url=resolved_url,
        captured=captured or date.today().isoformat(),
        body=body,
        extra=extra or None,
    )


def write_source(vault: Path, section: str, filename: str, content: str) -> Path:
    """Write `content` to `raw/<section>/<filename>`, refusing to clobber or leak.

    Three guards, each the same one the rest of the engine already applies: it must be a vault
    (load_config), the section must be one this vault declares and must be **public** — a raw
    source is meant to be ingested, and a private section may never be (ADR-0006) — and an
    existing file is never overwritten, because `raw/` is immutable ground truth.
    """
    vault = vault.expanduser().resolve()
    load_config(vault)  # raises VaultError when it isn't a vault

    if section not in wiki.public_sections(vault):
        if section in wiki.private_sections(vault):
            raise VaultError(f"raw/{section}/ is private — a source there could never be ingested")
        raise VaultError(f"this vault has no public raw/{section}/ section")

    path = vault / "raw" / section / filename
    if path.exists():
        raise VaultError(
            f"{path} already exists — raw sources are immutable, so it won't overwrite"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def source_filename(captured: str, title: str) -> str:
    """`<captured>-<slug>.md` — dated first so a directory listing sorts chronologically."""
    return f"{captured}-{slugify(title)}.md"


def _scalar(fields: dict[str, str], key: str) -> str:
    """A frontmatter value with one pair of surrounding quotes removed — Clipper quotes titles."""
    value = fields.get(key, "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _require_title(title: str) -> str:
    title = title.strip()
    if not title:
        raise VaultError("a source needs a title — it's the H1 and the filename")
    return title


def _strip_frontmatter(text: str) -> str:
    """The markdown below a leading `---` frontmatter block, or the whole text when there is none.

    Mirrors wiki.read_frontmatter's notion of a block — a `---` on the first line, closed by the
    next `---` — so what this drops is exactly what that parsed.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[index + 1 :]).lstrip("\n")
    return text  # unterminated block: leave the content alone rather than eating all of it
