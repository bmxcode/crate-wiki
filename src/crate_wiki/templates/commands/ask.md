---
description: Answer a question from the wiki, and promote the answer to a page if it's worth keeping.
argument-hint: "[a question]"
allowed-tools: Bash(crate:*), Read, Write, Edit, Glob, Grep
---

Answer a question from this wiki.

Two rules make this cheap and make it compound. **You read the map before the pages** — `index.md` first, then only the pages it points at — so a question costs a handful of reads, not the whole wiki. And **an answer worth keeping becomes a page**: without that, every answer evaporates into this chat and the wiki learns nothing from being asked. Both are enforced by the phases below. Don't collapse them.

Read `CLAUDE.md` and `CONVENTIONS.md` first if you haven't this session. `CONVENTIONS.md` is this vault's own rules — how it titles pages among them — and they apply here even though this file doesn't repeat them. `raw/` is immutable, and you shouldn't need it: the wiki is what you answer from. If the honest answer is "the wiki doesn't know," say that rather than mining `raw/` to manufacture one.

## Phase 1 — read the map, then only what it points at

$ARGUMENTS is the question. If it's empty, ask me what to look up and stop.

Read `index.md`. It's generated, one line per page, and it is your map of the wiki. From those one-liners pick the pages the question plausibly touches, and read **only those**. Never `ls wiki/`, never read a page you can't name a reason for — the same discipline `/ingest` uses, for the same reason.

If the index is empty, or nothing in it bears on the question, say so and stop. An empty wiki can't answer, and a confident answer with nothing under it is the failure this whole tool exists to avoid.

## Phase 2 — answer, then propose

Answer me in chat first. Prose, a table, or a chart — whatever fits the question. A chart is a `mermaid` fenced block, which Obsidian renders; don't reach for images. Cite the pages you leaned on, inline, so I can check the answer against them. Say which parts are grounded in a page and which are your inference, and name what the wiki doesn't cover.

Then judge whether this answer is worth keeping. It is, if someone would want it in three months and it isn't already sitting on a page. A throwaway lookup — a date, a name, a one-off — is not; say so and stop, and write nothing.

If it is worth keeping, propose a synthesis and **stop before writing anything**:

1. **A title.** It's the filename, the H1, and the `[[wikilink]]`, so it can't contain `? : " / [ ] | # ^ * < >` — which means it can't be the question phrased as a question. Make it a short **declarative claim**: the answer in a line. `Capture stays free by running on a hook`, not `How does capture stay free?`.
2. **The `summary:`** — one line, what `index.md` will show for it.
3. **The pages it draws from**, as `[[links]]` — the ones your answer rests on.

**Then wait for my reply. Do not create, write or edit any file in this phase.** The proposal is what I'm reviewing; writing the page and then asking spends the tokens the review exists to save. If I say no, the answer stays in this chat and that's fine — not every question earns a page.

## Phase 3 — promote, once I've approved

Scaffold the page — don't hand-write frontmatter:

```
crate new synthesis "<Title>" --vault .
```

Then, on the page:

- Fill `summary:` in the frontmatter — one line, and it's what `index.md` will show. A page without one shows up in the index as missing.
- Under the H1, restate my question verbatim, so the page records what it was answering.
- Write the answer as prose under `## Answer`, then `## What it rests on` and `## What would change it`. Write for someone who has forgotten everything and never saw this chat.
- Link every source page inline, in the sentence where it comes up: `the [[Session Parser]] drops tool output`, not a "Related" list at the bottom. That fan-out is the compounding.
- **Never link a page you haven't created.** A dead wikilink is a lie about what the wiki knows.

Record what the answer was built from — don't hand-edit the frontmatter:

```
crate extend "<Title>" --source "[[<a page you drew from>]]" --vault .
```

Run it once per source page. On a synthesis, `sources:` is the wiki pages behind the answer — the same field a source page uses for its raw files, because both are just provenance. It's how the answer stays traceable, so it's not a field to edit by hand.

Then close the loop:

```
crate fmt --vault .
crate index --vault .
crate log ask --title "<Title>" --vault .
```

All three are mechanical and none is optional. `crate fmt` puts each paragraph back on one line, because Obsidian renders a single newline inside a paragraph as a line break. `crate index` regenerates `index.md` from the `summary:` you just wrote, so don't edit `index.md` by hand; your edits get discarded.

Finish by telling me the page you wrote and what you deliberately left off it.
