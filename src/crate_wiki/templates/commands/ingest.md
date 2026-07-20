---
description: Fold new raw sources into the wiki, after discussing them with me.
argument-hint: "[raw path, or blank for everything pending]"
allowed-tools: Bash(crate:*), Read, Write, Edit, Glob, Grep
---

Fold new raw sources into this wiki.

This is the expensive operation — one source usually touches 10–15 pages. Two rules make it
affordable and make it worth doing at all: **you discuss before you write**, and **you never read
a page you can't justify**. Both are enforced by the phases below. Don't collapse them.

Read `CLAUDE.md` first if you haven't this session. `raw/` is immutable — you read it, never write it.

## Phase 1 — find and read the source

```
crate pending --vault .
```

$ARGUMENTS names a raw path to ingest. If it's empty, use the pending list: take the oldest entry,
or ask me which if there are several and they look unrelated.

Nothing pending, and no argument? Say so and stop. Don't go looking for work.

Read the raw file. Read **only** that file. A `stale` marker means the source page exists but the
raw file grew since — you'll be extending that page, not creating it, so read it too.

## Phase 2 — discuss, and then stop

Read `index.md`. It's generated, one line per page, and it is your map of the wiki.

From those one-liners pick the pages this source plausibly touches, and read **only those**. Never
`ls wiki/`, never read a page you can't name a reason for. If the index is empty, this is an early
vault and most things here will be new pages — that's fine and expected.

Now write me:

1. **The takeaways.** What this source actually establishes, in prose. Not a summary of what
   happened — the part worth keeping in three months. Say which are inferences.
2. **The page plan**, as a numbered list. For each: `create <type> "<Title>"` or `extend [[Page]]`,
   and one line on what goes there. Extend before you create — a new fact about an existing thing
   belongs on that thing's page, and new pages are for new things, not new mentions.
3. **Contradictions**, if this source disagrees with a page. Show me both sides. Never resolve one
   silently.
4. **What you're unsure about.** A named gap beats a confident page that's wrong.

**Then stop and wait for my reply. Do not create, write or edit any file in this phase.** Not a
draft, not a stub, not "just the source page". The plan is the thing I'm reviewing, and reviewing it
after you've written it costs the tokens the review exists to save.

If the source genuinely holds nothing worth keeping, say so and propose the minimal path: a source
page that records honestly that there was nothing here, and no fan-out. It still gets a page — that
is what stops it coming back as pending forever.

## Phase 3 — write, once I've replied

Work the plan I approved, including any changes I made to it.

Scaffold every new page — don't hand-write frontmatter:

```
crate new <type> "<Title>" --vault .
crate new source "<Title>" --vault . --raw raw/sessions/claude-code/<file>.md
```

`--raw` is what makes re-running `/ingest` skip this source instead of duplicating it, so the source
page's path has to be the real one.

Then, on each page:

- Fill `summary:` in the frontmatter — one line, and it's what `index.md` will show. A page without
  one shows up in the index as missing.
- Write prose, not bullets of bullets. You're writing for someone who has forgotten everything.
- Link inline, in the sentence where the thing comes up: `the [[Session Parser]] drops tool output`,
  not a "Related" heading at the bottom. Fan out generously — that fan-out *is* the compounding.
- **Never link a page you haven't created.** A dead wikilink is a lie about what the wiki knows.
- On a page you're extending, set `updated:` to today and leave `created:` alone.

Then close the loop:

```
crate index --vault .
crate log ingest --title "<the source's title>" --vault .
```

Both are mechanical and neither is optional. `crate index` regenerates `index.md` from the
`summary:` you just wrote — so don't edit `index.md` by hand, your edits get discarded.

Finish by telling me what you wrote and what you deliberately left out.
