---
description: Check the wiki for what code can't check — contradictions, superseded claims, missing pages, gaps.
argument-hint: "[a page or topic to scope the pass to, or blank for the whole wiki]"
allowed-tools: Bash(crate:*), Read, Glob, Grep, WebSearch
---

Check this wiki for the things that don't have a single right answer.

Two rules make this worth running. **You read `crate lint` before you read anything else** — the mechanical faults are already found, for free, and re-deriving them by hand is the one way to spend tokens on this operation and learn nothing. And **you write nothing at all**: a linter that edits the pages it's checking is one whose output you can no longer trust. Both are enforced by the phases below. Don't collapse them.

Read `CLAUDE.md` and `CONVENTIONS.md` first if you haven't this session. `CONVENTIONS.md` is this vault's own rules, and a page that follows them isn't wrong for looking unlike the others. `raw/` is immutable, and you shouldn't need it here — this is a pass over `wiki/`, not a re-ingest.

## Phase 1 — read what the code already knows

```
crate lint --vault .
```

One finding per line, tab-separated: the file, the check, and a detail.

| Check | What it means | Who fixes it |
|---|---|---|
| `dead-link` | a `[[wikilink]]` with no page behind it | me — the page gets created, or the link goes |
| `orphan` | an entity, concept or synthesis no other page links to | me, usually by linking it from somewhere real |
| `index-stale` | `index.md` no longer matches the pages on disk | `crate index` |
| `private-source` | a page cites a raw source in a **private** section | me, urgently — see below |
| `missing-source` | a page cites a raw path that isn't there | me |

Printed nothing? Then nothing mechanical is wrong, which is the normal state and not a reason to go hunting. It exits 0 either way; a non-zero exit means the vault itself is bad, not that it found something.

**Don't re-derive any of that by hand.** Don't grep for `[[`, don't count inbound links, don't compare `index.md` against anything. Those questions have one right answer and it has already been computed.

**And don't report staleness.** Whether a raw file has outrun the page written from it is `crate pending`'s question, answered against the digest each page recorded — a card being rewritten after a page read it is ordinary, not a fault. If that's what you want to know, run `crate pending --vault .` and say so.

A `private-source` finding is the one to raise first and on its own. It means something derived from a section that never leaves this machine has been written into `wiki/`, which is committed. Name the page and the section, say the page needs reviewing before the vault is pushed anywhere, and don't quote what's in it.

## Phase 2 — the four questions only you can answer

Read `index.md`. It's generated, one line per page, and it is your map. $ARGUMENTS names a page or a topic to scope this pass to; if it's empty, the whole wiki is in scope — but that means scanning the one-liners, not reading every page.

**Read only the pages you can name a reason for**, the same discipline `/ingest` and `/ask` use, for the same reason: a lint over fifty pages that reads all fifty costs more than the ingest that wrote them. The one-liners are what tell you which pages might disagree; two summaries that make competing claims about the same thing are the signal to open both.

Then answer these four, and only these four:

1. **Contradictions.** Two pages that can't both be true. Show me both sides, quote the sentence from each, and say which you'd believe and why. **Never resolve one silently** — that's the rule the whole wiki rests on.
2. **Claims a newer source superseded.** A page that was right when it was written and isn't now, because something later says otherwise. This is about the *claim*, not the file: `sources:` and `updated:` tell you which page saw the later material, and the page that didn't is the one carrying the stale sentence.
3. **Concepts referenced but with no page.** Something named across several pages, load-bearing, and never linked because there's nothing to link to. A dead `[[wikilink]]` is already in Phase 1 — this is the opposite case, where the wiki has been careful enough not to link a page that doesn't exist and the gap left no trace.
4. **Gaps a search would close.** A named unknown on a page — a version, a date, a spec — that a web search would settle. Search where it's genuinely cheap and quotable, and say what you found and where. Don't research the whole topic; this is filling a hole the page already admits to.

Nothing to say under a heading? Say "nothing" and move on. A lint that manufactures a finding per heading is one you learn to skim.

## Phase 3 — report, and stop

Write me the findings in chat, grouped by the four questions, most consequential first. For each: the pages involved, the evidence, and what you'd do about it.

**Then stop. Do not create, write or edit any file — not now and not in this operation at all.** Not a fix, not a stub, not the page that would clear an orphan, and not `index.md`. What you found is what I'm reading, and a pass that quietly repaired half of it is one I can't check. Every fix here belongs to an operation that owns it:

| What you found | What runs |
|---|---|
| a concept with no page | `/ingest`, from the source that established it |
| a question worth a page | `/ask` |
| a contradiction | me, once I've decided which side is right |
| `index-stale` | `crate index --vault .` |

Finish by saying what you deliberately didn't look at — the pages you skipped and why. A lint that doesn't say where it stopped reads as a clean bill of health for the whole wiki, and it never is one.
