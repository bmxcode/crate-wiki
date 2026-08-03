---
description: Fold new raw sources into the wiki, after discussing them with me.
argument-hint: "[raw path, or blank for everything pending]"
allowed-tools: Bash(crate:*), Read, Write, Edit, Glob, Grep
---

Fold new raw sources into this wiki.

This is the expensive operation — one source usually touches 10–15 pages that already exist. Two rules make it affordable and make it worth doing at all: **you discuss before you write**, and **you never read a page you can't justify**. Both are enforced by the phases below. Don't collapse them.

That 10–15 is a count of pages *touched*, not created. In an early vault where little exists yet, the right answer is a handful of pages that each say something — not a dozen stubs written to hit a number.

Read `CLAUDE.md` and `CONVENTIONS.md` first if you haven't this session. `CONVENTIONS.md` is this vault's own rules — how it titles pages among them — and they apply here even though this file doesn't repeat them. `raw/` is immutable — you read it, never write it.

## Phase 1 — find and read the source

```
crate pending --vault .
```

$ARGUMENTS names a raw path to ingest. If it's empty, use the pending list: take the oldest entry, or ask me which if there are several and they look unrelated.

A line reading `N Codex rollouts not yet swept` isn't a source to ingest — it means `/fetch-codex` hasn't been run since those sessions happened. Run `/fetch-codex` first, then come back to `/ingest`.

Nothing pending, and no argument? Say so and stop. Don't go looking for work.

Read the raw file. Read **only** that file. A `stale` marker means the source page exists but the raw file grew since — you'll be extending that page, not creating it, so read it too.

A `live` marker means that card is the session you're in right now. It's still being written — every reply you make changes it, including this ingest — so a page written from it would be a partial record that reads as a complete one. Skip it, say that's why, and take the next entry. If `live` is the only thing pending, say so and stop; it'll be there, finished, once this session ends.

## Phase 2 — discuss, and then stop

Read `index.md`. It's generated, one line per page, and it is your map of the wiki.

From those one-liners pick the pages this source plausibly touches, and read **only those**. Never `ls wiki/`, never read a page you can't name a reason for. If the index is empty, this is an early vault and most things here will be new pages — that's fine and expected.

Now write me:

1. **The takeaways.** What this source actually establishes, in prose. Not a summary of what happened — the part worth keeping in three months. Say which are inferences.
2. **The page plan**, as a numbered list. For each: `create <type> "<Title>"` or `extend [[Page]]`, and one line on what goes there. Extend before you create — a new fact about an existing thing belongs on that thing's page, and new pages are for new things, not new mentions.

   `<type>` is **`source`, `entity` or `concept`** — those are the three this operation writes. One `source` page per raw file, always. `entity` is a person, project, repo, system or company; `concept` is an idea, pattern or technique, and a format or a protocol is a concept, not an entity. If something fits none of the three, say so rather than bending one to fit.
3. **Contradictions**, if this source disagrees with a page. Show me both sides. Never resolve one silently.
4. **What you're unsure about.** A named gap beats a confident page that's wrong.

**Then stop and wait for my reply. Do not create, write or edit any file in this phase.** Not a draft, not a stub, not "just the source page". The plan is the thing I'm reviewing, and reviewing it after you've written it costs the tokens the review exists to save.

If the source genuinely holds nothing worth keeping, say so and propose the minimal path: a source page that records honestly that there was nothing here, and no fan-out. It still gets a page — that is what stops it coming back as pending forever.

## Phase 3 — write, once I've replied

Work the plan I approved, including any changes I made to it.

Scaffold every new page — don't hand-write frontmatter:

```
crate new <type> "<Title>" --vault .
crate new source "<Title>" --vault . --raw raw/sessions/claude-code/<file>.md
```

`--raw` is what makes re-running `/ingest` skip this source instead of duplicating it, so the source page's path has to be the real one.

The title becomes the filename and the H1, so it's the thing to get right first. Follow `CONVENTIONS.md` wherever it says anything about titles — that's where a vault records how it names a kind of page, and this file deliberately doesn't, because vaults don't agree.

**Don't write `wiki/daily/` or `wiki/syntheses/` here.** A daily page is one *day* across every source, which is `/daily`'s job, and a synthesis answers a question you asked, which is `/ask`'s. Writing either from a single source claims a scope you don't have and leaves those operations reconciling pages they didn't write. If a day or a question is worth a page, say so and I'll run the operation that owns it.

Then, on each page:

- Fill `summary:` in the frontmatter — one line, and it's what `index.md` will show. A page without one shows up in the index as missing.
- Write prose, not bullets of bullets. You're writing for someone who has forgotten everything.
- Link inline, in the sentence where the thing comes up: `the [[Session Parser]] drops tool output`, not a "Related" heading at the bottom. Fan out generously — that fan-out *is* the compounding.
- **Never link a page you haven't created.** A dead wikilink is a lie about what the wiki knows.

On a page you're **extending** rather than creating, record it — don't hand-edit the frontmatter:

```
crate extend "<Title>" --source "[[<the source page you just wrote>]]" --vault .
```

That bumps `updated:`, leaves `created:` alone, and adds the source only if it isn't already there. On a source page `sources:` is what makes `/ingest` skip work it has already done, so it's not a field to edit by hand.

Then close the loop:

```
crate fmt --vault .
crate index --vault .
crate log ingest --title "<the source's title>" --vault .
```

All three are mechanical and none is optional. `crate fmt` puts each paragraph back on one line, because Obsidian renders a single newline inside a paragraph as a line break — don't hand-wrap prose to a column, and don't worry if you did. `crate index` regenerates `index.md` from the `summary:` you just wrote, so don't edit `index.md` by hand; your edits get discarded.

Finish by telling me what you wrote and what you deliberately left out.
