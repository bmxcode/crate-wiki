# ADR-0017 · Staleness is a content comparison, recorded on the page

**Status:** accepted · 2026-08-02

## Context

`crate pending` reports a raw source as `stale` when the file has outrun the page written from it. Since D4 that check has been a date comparison: the raw file's mtime, truncated to a day, against the page's `updated:` frontmatter.

Both halves of that are wrong, in opposite directions.

**It cannot see the common case.** The Stop hook rewrites a session's card in place while the session runs, so a card that was ingested mid-flight keeps growing that same afternoon — and `"2026-07-24" > "2026-07-24"` is false, so a same-day rewrite is invisible by construction. Issue #22 has the live example from crate-personal: a source page ingested mid-session, the session then roughly doubled in length, and `crate pending --all` still calls that card `ingested`. The page is a partial record of a session and nothing will ever say so. It would only have flagged if the session happened to span midnight.

**And it fires on a fresh clone, for every page at once.** Git does not preserve mtimes, so after `git clone` every raw file carries checkout time — newer than every `updated:` in the vault — and the whole ledger reports `stale` on a machine where nothing is wrong. This repo has already rejected mtime comparison once for exactly this reason: [ADR-0010](0010-conventions-file-and-upgrade-baseline.md) chose sha256 for `.crate/baseline.json` because "a `git checkout` rewrites every mtime in a vault, so a timestamp would report a fresh clone as edited and nothing else would be wrong with it." The ingest ledger simply never got the same treatment.

[ADR-0016](0016-a-rewind-re-renders-the-day-it-changed.md) then gave `stale` a second meaning the date check was never built for. A Claude Code rewind can re-render a card that was already ingested, and the re-rendered card can be *shorter* — so a page may now describe work its source no longer contains, not only lack work its source gained. A date says nothing about direction, or about whether anything changed at all.

## Decision

**A source is stale when its content digest differs from the digest the page recorded when it read that source.**

**The record lives in `source_hash:` frontmatter, beside `sources:`.** The ingest ledger is already the `sources:` field rather than a state file, and [ADR-0008](0008-code-and-prompt-inside-an-operation.md) chose that because it is committed, travels with the vault, and **self-heals** — delete a source page and its raw file is pending again. The digest answers a question about the same pairing and has to live in the same place, or the two halves of one ledger acquire different lifetimes.

**Each entry names its own path**, `"<raw path> <digest>"`, split on the last whitespace. `sources:` is a list, so this must be one too, and a positional parallel list would silently pair a path with another file's hash the first time anyone reorders either one. Splitting on the *last* space rather than the first keeps it correct for raw paths containing spaces, which `raw/` genuinely holds — it takes clips and pasted documents, not only session cards. An entry naming a path that is no longer in `sources:` is never looked up, and a malformed entry is skipped: this reads a ledger, so it degrades rather than raising.

**The digest is a 12-character sha256 prefix**, where `baseline.json` keeps the full 64. This one is written into a page's frontmatter and read by a human in Obsidian's properties panel; it detects a rewrite, it does not defend against a forged one, and the baseline can afford to be unreadable because nothing reads it by eye.

**A page with no recorded digest for a file falls back to the old date comparison.** Every page in every existing vault is in that state. The fallback keeps their behaviour exactly as it was — rather than silently reporting a whole vault as fresh, which is the failure this record exists to fix — and a page upgrades itself the first time `crate extend --source` touches it, which is what `/ingest` does when it absorbs the rest of a resumed session.

**No backfill.** Hashing every already-ingested pair once, at upgrade, would record whatever each card says *today* as the state that was ingested — permanently marking the known-partial crate-personal page as fresh. That writes the bug into the ledger and makes it unrecoverable, where the fallback merely leaves it visible. ADR-0010 reached the same fork for the baseline and answered the same way: a file the engine has no record of is left alone, because the engine cannot tell.

`crate pending`'s statuses do not change. `stale` already meant this; it just could not detect it.

## Alternatives rejected

**Put a timestamp in `updated:` instead of a date.** The obvious minimal fix: same mechanism, finer granularity, no new field. Rejected because it does not fix the clone case at all — and makes it worse. It sharpens the *committed* side of the comparison while the other side is still an mtime that git does not preserve, so a fresh clone still reports every page stale, now with more precision. It also changes what `crate extend` writes on every page of every vault and makes the field noisier for a human reader, in exchange for a strictly weaker guarantee than hashing.

**Record the card's own `ended:` frontmatter**, which the session parser already writes. Free, already committed, no new format. Rejected: it describes the session, not the file. A card re-rendered with the same last timestamp — a metadata change, a re-parse under a new engine version, a rewind that drops records from the middle of a day — is identical under this test, and after ADR-0016 those are real shapes rather than hypotheticals.

**A sidecar file, `.crate/ingested.json`, mirroring `baseline.json`.** Symmetrical with the baseline, keeps page frontmatter unchanged, and the hashing code is already there. Rejected on lifetime: it breaks the self-healing property the frontmatter ledger was chosen for. Delete a source page and the raw file correctly becomes pending again — but its digest record would linger in a file nothing points at, and the two halves of one ledger would drift apart exactly when someone is cleaning up. The baseline can be a sidecar because it describes files the *engine* owns; this describes a relationship between two files the *vault* owns.

**One combined digest per page, over all its sources.** Fewer entries, no encoding question. Rejected because `pending` reports per raw file: a page citing three cards where one changed would mark all three stale, and the two it was right about would teach you to ignore the one it wasn't.

## Consequences

**Good.** The failure that motivated this — a partial source page that is permanently silent about being partial — now surfaces on the next `crate pending`, which is what `/ingest` runs first. A fresh clone stops reporting an entire vault as stale. Both directions of change are caught, so ADR-0016's accepted cost (a card that shrinks under a rewind) has somewhere to show up rather than being invisible. And the comparison no longer depends on filesystem metadata at all, so it survives a clone, a restore, a sync, and CI's `CRATE_TEST_CLOCK` run without anyone thinking about it.

**Bad.** A new frontmatter field on every source page, visible in Obsidian, holding a value no human will ever read on purpose. `source.md`'s template changes, so every vault sees it as `updated` on the next `crate upgrade` — the baseline mechanism working as designed, but still one more diff to look at. And staleness now costs a read of each raw file where it used to cost a `stat()`; that is bounded by the number of *ingested* sources and each is a card rather than a transcript, so it stays well inside what `crate pending` can do on every `/ingest`.

**The fallback is a second code path with a shelf life.** Two rules answer "is this stale" until the last pre-D21 page in a vault has been re-extended, and the older rule is the one with the known bugs. It is deliberate — the alternative was a backfill that lies — but it is a thing to delete later, not a thing that has been fixed.

**Shared with the linter.** `source_digest` and `recorded_digests` are named helpers rather than logic inlined in `_is_stale`, because D8 (#9) wants the same primitive to detect a raw file edited after synthesis. #22 asked whether the two share a mechanism; they do, and this is it.
