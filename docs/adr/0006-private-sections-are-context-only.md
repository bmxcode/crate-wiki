# ADR-0006 · Private raw sections are context-only, never synthesized

**Status:** accepted · 2026-07-17

## Context

The personal vault holds a journal. It's more sensitive than the rest of what's in there — the personal repo has a private remote, and the journal is the one thing I'd rather never leave the machine at all.

The obvious answer is one line: gitignore `raw/journal/`. The raw text stays local, everything else syncs, done in five seconds.

It doesn't hold, and the reason is the architecture itself. Layer 2 is *synthesized from* Layer 1. An `/ingest` of a journal entry reads the ignored file and writes its substance into `wiki/` — which is committed, and pushed. The gitignore never fires, because nothing under `raw/journal/` moved; the conclusion did, in a page that looks like every other page.

So the gitignore protects the letter of the rule and can do nothing about the spirit. It's a file-path rule guarding against a data-flow problem, and the flow it needs to stop runs straight through it.

That leaves the boundary somewhere only the schema can hold it: what the LLM is willing to write down.

Since sections are config (`[[raw.sections]]` with a `private` flag) rather than a hardcoded case for `journal`, the rule is worth stating about private sections generally rather than about the journal specifically.

## Decision

A section marked `private = true` in `.crate/config.toml` is **readable for context and never a source for a page.**

The LLM may read it to answer a question. It may not write anything derived from it into `wiki/`, in any form — not a summary, not a conclusion, not an entity page that exists because of it. If something in a private section deserves a page, it says so and I decide.

Two mechanisms, because one isn't enough:

| | |
|---|---|
| `.gitignore` | keeps the raw text out of git |
| the vault's `CLAUDE.md` | keeps its substance out of `wiki/` |

The gitignore alone is the version that doesn't work.

## Alternatives rejected

**Only the raw text stays local; distilled insight may land in `wiki/`.** The reading that says a gitignore is about bulk unfiltered text, not ideas — and it has a real argument, since compounding what I think is the entire point of the project. Rejected because it reads the rule as being about *file size* when it's about *content*. A journal entry's conclusion is the sensitive part; the surrounding text is just the part that's long.

**No special rule at all — treat journal as an ordinary source and drop the gitignore.** Honest, at least: it doesn't pretend to a boundary it isn't holding, and it's strictly better than a gitignore that lulls me into thinking the problem is solved. Rejected because the boundary is worth actually holding, and this discards it rather than fixing it.

**Ingest the journal, but only into pages that are themselves gitignored.** Keeps the compounding and the isolation. Rejected: it splits `wiki/` into synced and unsynced halves, so every page needs a second question asked about it before it's written — and the linter, the index, and `/ask` would all need to understand the split. That's a lot of machinery in the highest-traffic part of the system to protect the smallest one.

## Consequences

**Good.** The rule sits where the decision actually gets made — at the moment of writing a page — rather than at a file path the data never crosses. It generalizes: any section can be marked private later and inherits the whole rule, in config, without touching code.

**Bad.** Some genuinely useful journal insight never compounds. That's the price, and it's a real one: the material most worth thinking about is often the material I'd least like to sync.

**Also bad, and worth saying plainly.** Gitignoring a section is precisely what makes it private, which means **a private section has no offsite backup.** Time Machine is the only copy. That's the same weak joint ADR-0001 names for the work vault, now reappearing inside the personal one — and it's a poor state for the part of the vault holding what I'd least like to lose.

**This rule is unenforceable by code, which is unusual here.** ADR-0004 says anything with a single right answer is code, and "would this page leak a private section?" doesn't have one — it's judgment about where a claim came from. The linter can flag a `sources:` entry pointing at a private path, and D8 should. It cannot catch a page that quietly knows something it shouldn't.
