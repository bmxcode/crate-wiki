---
description: Write the account of a day, from that day's session cards.
argument-hint: "[a date, or blank for yesterday]"
allowed-tools: Bash(crate:*), Read, Write, Edit, Glob, Grep
---

Write the account of a day in this vault.

This is the operation the rest of it was built for: *what did I do yesterday?*, answered from what actually happened rather than from memory. Two rules make the answer worth having. **You work from the day's session cards** — they're the record of the day; the wiki is only what's been made of it since. And **you tell me the day before you write it**, because the cards hold what happened and only I hold why. Both are enforced by the phases below. Don't collapse them.

Read `CLAUDE.md` and `CONVENTIONS.md` first if you haven't this session. `CONVENTIONS.md` is this vault's own rules, and they apply here even though this file doesn't repeat them. `raw/` is immutable — you read it, never write it.

## Phase 1 — gather the day

```
crate day $ARGUMENTS --vault .
```

$ARGUMENTS is the day: a date, `today`, or blank for yesterday. **Don't work the date out yourself and don't pass one you inferred** — you don't reliably know today's date, and a page titled for the wrong day looks exactly like one titled for the right day. `crate day` resolves it and prints the day it resolved on the first line; every line after that is a session card, oldest first.

Read every card it lists, in that order. All of them — a day is a handful of cards, and the order is the day. Don't go looking in `raw/` beyond that list: a card the command didn't print isn't part of this day.

Nothing printed but the date? Say the day was quiet, and stop. Don't widen to the day before, and don't reconstruct a day out of git.

Then read `index.md`. It's generated, one line per page, and it's your map of what the wiki already knows — it's how you find the pages to link, not only how you avoid linking ones that don't exist. Scanning its one-liners is free; do it thoroughly. Read the pages behind it sparingly: a name you have to get right, a claim you want to check. If the index already lists a page for this day, read it — you'll be extending it rather than writing it, and say so when you propose.

## Phase 2 — tell me the day, then stop

Write me the account in chat, as prose:

1. **What the day was actually about.** The thread through it, not a session-by-session replay. Four sessions on one problem are one story; a day that genuinely went three ways went three ways, and saying so is the honest answer.
2. **What moved.** What's different at the end of the day than at the start — shipped, decided, abandoned. Abandoned counts: a rewind in a card is a thing that happened, and it's the part I'll have forgotten.
3. **What I learned**, if anything. The part still worth knowing in three months.
4. **Loose ends.** What was left mid-air, and what the next day picks up.
5. **What the cards can't tell you.** A card holds prompts, prose and actions — never why, and never what happened away from the keyboard. Name the gaps rather than filling them.

Then propose the page: the `summary:` line, and the pages it will link. Build that list deliberately — go back through the account and, for every project, concept, person or session you named, check `index.md` for a page that already covers it. Those existing pages *are* the links, and finding them is the point: a day that reads as being *about* something the wiki already has a page for, and links nothing to it, has dropped the fan-out that makes the wiki compound. The most on-topic page is the one it's easiest to forget you already wrote.

**Then stop and wait for my reply. Do not create, write or edit any file in this phase.** This round is what makes the page worth keeping — the cards record what happened and I supply why — and I can't correct an account you've already written.

If the day holds nothing worth a page, say so and propose skipping it. Not every day earns one, and `wiki/daily/` is worth reading only while every page in it is.

## Phase 3 — write it, once I've replied

Work the account I approved, including my corrections. Those are the point — don't quietly re-assert what I changed.

**If a page for this day already exists**, extend it rather than replacing it: `crate new` will refuse, rightly. Keep what's there, add what the cards it doesn't already list in `sources:` say, and run `crate extend` for those cards. A day gets revisited when a session was resumed or landed late; it doesn't get rewritten.

Otherwise, scaffold it — don't hand-write frontmatter:

```
crate new daily "<YYYY-MM-DD>" --vault .
```

The title is the date `crate day` resolved, so the filename, the H1 and the `[[wikilink]]` are all that date. `created:` will be today — the day the page was written, not the day it covers. That's correct; don't edit it.

Then, on the page:

- Fill `summary:` — one line, and it's what `index.md` will show. `Shipped the session parser, then rewrote the ledger it broke` beats `Work on the engine`.
- Write prose under the headings the template gives you. An account, not a changelog: someone who has forgotten the day should be able to read it and have the day back.
- Link inline to pages that exist — `spent the morning on the [[Session Parser]]`. **Never link a page you haven't created**, and don't create one to fix a dead link.
- Don't quote the cards at length. The card is still there and always will be; the page is what the day *meant*.

**Write exactly one page.** A day that surfaced a genuinely new project, concept, or source worth its own page is telling you to run `/ingest` — say so and leave those pages to it. Writing them here claims a scope a single day doesn't have. But check `index.md` before you flag anything: if the page already exists, it isn't `/ingest`'s job and it isn't new — it's a link you missed, so add it here.

Record the cards it was written from — don't hand-edit the frontmatter:

```
crate extend "<YYYY-MM-DD>" --source "raw/sessions/claude-code/<card>.md" --vault .
```

Once per card. On a daily page `sources:` is the raw cards behind the account — the layer this page read, the same way a source page cites its raw file. It doesn't mark those cards as ingested: the ingest ledger is read only off `wiki/sources/` pages, so `/ingest` still has them pending, which is right. A day's account is not a summary of a source.

Then close the loop:

```
crate fmt --vault .
crate index --vault .
crate log daily --title "<YYYY-MM-DD>" --vault .
```

All three are mechanical and none is optional. `crate fmt` puts each paragraph back on one line, because Obsidian renders a single newline inside a paragraph as a line break. `crate index` regenerates `index.md` from the `summary:` you just wrote, so don't edit `index.md` by hand; your edits get discarded. The log entry's own date is today and its title is the day covered — those differ whenever you write up a day after the fact, and that's the honest record.

Finish by telling me the page you wrote and what you deliberately left off it.
