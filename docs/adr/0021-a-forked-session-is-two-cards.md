# ADR-0021 · A forked session is two cards, and crate leaves it alone

**Status:** accepted · 2026-09-01

## Context

[ADR-0016](0016-a-rewind-re-renders-the-day-it-changed.md) closed by naming a constraint it carried forward: the corpus probe for issue #39 had turned up one instance of a shape the append-on-resume model did not cover — **a new transcript file that begins with a verbatim copy of a prior conversation's records, re-stamped under a new `sessionId`**, with new work chained on. One instance establishes that the shape exists; it says nothing about what causes it, and the format records no marker distinguishing a resume from a rewind from a crash. [Issue #41](https://github.com/bmxcode/crate-wiki/issues/41) held that open, explicitly *not* ready to be built against — #32 is the standing lesson on designing against a format model inferred from too little evidence.

So the first move was the controlled run the observational probe could not do. On a throwaway scratch project outside this repo, each candidate trigger was driven deliberately and the transcripts on disk were diffed. Nothing from those runs — no id, no timestamp, no path, no count — enters this repo; what follows is only the qualitative shape, reproduced synthetically in the tests.

**The trigger is `--fork-session`.** It is a documented, opt-in Claude Code flag — "when resuming, create a new session ID." Plain `--resume` and `--continue` **append in place**: same file, same `sessionId`, exactly as ADR-0016 found. Only the fork flag produces a second file, and when it does the shape is #41's precisely:

- the fork's conversation records carry the predecessor's **`uuid`s and `timestamp`s unchanged**, under a **new `sessionId`**;
- the copied block is the predecessor's conversation in its entirety, and the fork's root record has **`parentUuid: null`** — it is self-contained, needing no pointer out of the file;
- new work is chained on by `parentUuid`;
- **nothing links the two files.** A copied record is byte-identical in its field set to a native one — there is no fork flag, no source field; the only `leafUuid` present points within the file to its own leaf. The sole trace of the relationship is the `uuid` overlap itself.

crate double-counts, confirmed by running the real parser over both files: the predecessor cards the work up to the fork point, and the fork — self-contained, so `_live_path` walks the copied prefix as its own — cards that same work again plus the divergent tail. Forks compound: a chain of forks cards the earliest turns once per link.

This is the D40 finding, and it is what makes the decision decidable: the cause is not corruption or a crash. It is a person deliberately branching a session.

## Decision

**crate does nothing. A forked session is two sessions, and it becomes two cards.**

Claude Code models a fork as two sessions — two ids, two files — because that is what the user asked for: preserve the original, explore a divergence. A card per session is crate reflecting that model faithfully, not failing to notice a duplicate. The overlap is real, and it is overlap the user created on purpose. The residual cost is stated plainly under Consequences; it is the acceptable-artefact-of-an-immutable-`raw/` outcome #41 itself named for the "rare and self-inflicted" case, and the experiment shows the trigger is exactly that — opt-in, never automatic.

## Alternatives rejected

**Detect and skip** — recognise that a successor's leading records duplicate another file's `uuid`s and card only the new ones. This is the tempting fix, and it fails on the same rock that stopped merge-on-capture in [#32](https://github.com/bmxcode/crate-wiki/issues/32). Detection is **inherently cross-file**: the experiment confirmed a copied record carries no marker and the two files share no link, so the only way to know the prefix is a copy is to compare `uuid`s against every other session file — forcing `parse`, which takes one session path, to glob the sessions directory. And it is worse here than it was for Codex. It runs on the fail-quiet Stop hook ([ADR-0002](0002-free-capture-paid-synthesis.md)), so it must never raise while reaching across the filesystem. It is order-dependent: the predecessor may be captured after the fork, or a fork may be taken from a session so old it was never captured, so "the original card" is not reliably there to diff against. It collides with `_live_path`, because the fork's root sits *inside* the copied prefix — skipping the prefix means re-rooting the walk at a divergence point that can only be found by the cross-file comparison. And it collides with the day split, since prefix and tail can fall on different local days. That is a great deal of fragile machinery on the capture path, to undo something a person did on purpose.

**Detect and link** — card both, and mark the successor as a fork of the predecessor. It carries all of skip's cross-file detection cost and then adds frontmatter nothing reads. `session_id` is already on every card; a `parent_id` pointing at the predecessor would be genuinely new, but [ADR-0015](0015-a-day-of-a-thread-is-a-card.md) established that no consumer exists for it — `/daily` reads a single day, and `crate day` groups on `started:`. The `thread_id`/`parent_id` frontmatter has been declined twice already (#32, ADR-0015); the experiment supplies no new reader, so it stays declined a third time.

**Clean up the duplicate after the fact.** Not available. `raw/` is immutable to Tier 1 — a card cited in a daily page's `sources:` cannot be deleted or renamed ([ADR-0016](0016-a-rewind-re-renders-the-day-it-changed.md)). The only place any of these fixes could act is at capture, which is where every objection above already lives.

## Consequences

**Good.** No new code on the fail-quiet capture path, and `parse` keeps its one-session-one-path contract — the seam ADR-0014 drew, and the line #32 held, both stay intact. The behaviour is predictable and explained rather than surprising: fork a session and you get a second card, the same way Claude Code gives you a second session.

**Bad, and worth stating.** When someone does fork, the shared prefix is carded twice — or, for a chain of forks, once per link — and `/daily` reading a day's cards, or `/ingest` owing each a source page, will meet that work more than once. `crate day` orders by `started:`, so a fork sits adjacent to its original where it reads as a repetition rather than an artefact. Nothing marks that the two overlap; the reader who notices two near-identical cards has to know that a fork is what they are seeing. This record is where that is written down. It is the price of not building cross-file, order-dependent detection against a shape a person creates deliberately, and it is the right price.

**Constraint carried forward.** This turns on `--fork-session` being opt-in on the version measured, and on default resume appending in place. If a future Claude Code makes forking the default path of an ordinary resume, the frequency assumption here is void and the trade reopens — this is the #32 caution, respected rather than dismissed: the decision is pinned to a behaviour that was measured, not assumed permanent. The fix, if it is ever needed, still has a place to go; this record is the account of why the place is, for now, deliberately empty.
