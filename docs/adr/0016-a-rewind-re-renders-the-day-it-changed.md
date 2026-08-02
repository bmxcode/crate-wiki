# ADR-0016 · A rewind re-renders the day it changed

**Status:** accepted · 2026-08-02

## Context

[ADR-0015](0015-a-day-of-a-thread-is-a-card.md) split a Codex thread into one card per local day it was active on, and closed by naming the constraint it carried forward: Claude Code sessions can also be resumed across days, and that deliverable deliberately did not look at whether their transcripts had the same problem.

They do. The resume model was validated against a real corpus before anything was built (issue #39, and issue #32 for why this project does not design against an inferred format model): **a Claude Code resume appends to the same JSONL file**, `sessionId` is constant within a file, no record's `parentUuid` points outside its own file, and the format carries no continuation-pointer field at all — there is no `parent_thread_id` analogue to misread. So one file is one session that can run for days, and `claude.py` had exactly the defect D36 fixed for Codex: a card dated to the day the session started, invisible to `crate day` on every later day it touched. The probe also settled the question D36 had to assume, and the later days carry real work rather than a stray tail record.

Applying ADR-0015's split to a second source is mechanical. One thing about it is not, and it is the whole reason this record exists.

**Codex writes an append-only log; Claude Code writes a tree.** ADR-0015's stability argument has two halves: every field of a day's card is a function of that day's records alone, *and* a day's records can never change once written, because the log only grows. The second half does not transfer. `_live_path` is recomputed over the whole file on every capture, and a rewind re-decides which records are live — so if the rewind point sits before midnight of a day already captured, that day's slice **shrinks retroactively**.

This is reachable and unquantified. Rewinding is an ordinary action and long in-file gaps are common, so a session that crosses midnight and is then rewound to before it is not exotic. How often it actually happens cannot be established by observation, because the format records no marker distinguishing a rewind from a resume — and issue #41 is the standing reminder not to build machinery against a shape nobody has measured.

## Decision

**The day a rewind changed is re-rendered in place, on its own cursor. No card is ever deleted or renamed.**

**A day's cursor is the uuid of that day's last live-path record.** Codex uses the day's record count, which is sufficient for a log that only grows. A path through a tree is determined by its endpoint — each record has exactly one parent — so one uuid identifies that day's entire slice, and it therefore moves when a rewind truncates the day just as it moves when an append extends it. A count would not reliably notice a rewind that replaced as many records as it dropped. This is what makes the re-render happen at all: `cards.write` compares cursors, so a changed day writes and an untouched day returns early without opening its file.

**A card's path cannot move under a rewind.** The filename is `<date>-<session id>.md`; the walk always retains the root it terminates on, so the first live record — and therefore the day key of the earliest day — is unchanged, and `sessionId` is constant within a file. A daily page's `sources:` entry resolves before and after. That is the constraint everything here is downstream of: `raw/` is immutable to Tier 1.

**A day left with no live records at all earns no card, and the card already written for it stays on disk** — stale, no longer regenerated, and never removed. Deleting it is not available, and would be the wrong trade anyway: a page may already cite it.

**The day slice is over the live path, not over every record.** A rewind's abandoned branch is not conversation that survived, so a day whose records were all rewound away has no live conversation on it.

**Each day's metadata is read from its own slice** — `cards._last` over the day's records rather than the whole live path, for `sessionId`, `cwd`, `gitBranch` and `version` alike. The probe found this fixes no measured problem: `claude.py` already took the *last* value, so unlike Codex's first-`session_meta` there was no misreported branch to correct. It is not there for symmetry with Codex either. It is there because "every field of a day's card is a function of that day's records alone" is the invariant the re-capture story rests on, and a file-wide `_last` breaks it silently — appending a day on a new branch would change what day one's card *should* say while day one's cursor stayed put, so the file on disk and the value the code computes would quietly disagree until something cleared the cursor.

**What moved into the core is one function.** `_day_key` — a timestamp and a fallback to the local calendar day ([ADR-0013](0013-local-session-timestamps.md)) — now lives in `cards.py` beside `_local` and `_parse_ts`, because both sources must cut a session at the same instant and adapters must not import each other. Nothing else moved. `codex._by_day` interleaves `session_meta` tracking with its grouping and `claude._by_day` runs over the live path; what they share is a four-line loop, and unifying that would mean a callback or a second pass for no gain. [ADR-0014](0014-shared-card-core-per-source-adapters.md)'s test holds, and honestly the answer is less than it first looks: the day *rule* is shared, the day *grouping* is not.

## Alternatives rejected

**Freeze a day's card once it has been captured.** The card that a daily page cited would then always be the card it was written from, which is the tidier promise. Rejected on three counts. It cannot be honoured: the only place to record "this day was captured larger" is `.crate/state.json`, which is explicitly disposable — `_load_state` rebuilds a corrupt cursor rather than failing, and a cleared cursor re-renders everything — so the guarantee evaporates the first time state is lost, which makes it a coin flip about whether a card matches the transcript rather than a guarantee. It is also not the status quo it sounds like: a rewind *within* a day already re-renders that card today, and has since D2, so freezing would be new behaviour introduced for a hazard rather than existing behaviour preserved. And it is the expensive side — re-rendering is what falls out of doing nothing special, while freezing means detecting shrinkage and suppressing a write, new code on the fail-quiet Stop-hook path ([ADR-0002](0002-free-capture-paid-synthesis.md)), against a shape nobody has measured.

The cost of choosing the other way is real and worth stating plainly: a daily page's account of Monday can be built from a card that later shrinks, and nothing marks that it did. That is honest — the transcript really did change — and it is better than a card that silently contradicts the file it claims to describe.

**Group every record by day, rather than the live path.** It would make an earlier day's slice append-only again, since a rewind adds records and never removes them, which would restore ADR-0015's stability argument wholesale. Rejected: it reinstates exactly what the tree walk exists to remove. A card would report work that was abandoned, presented as what happened — the failure named in `docs/architecture.md`'s Keep/Drop/Collapse table, arriving through the day split instead of through a flat read. A day made only of dead branches is a day made only of records the card model drops.

**An amendment to ADR-0015 rather than a new record.** Tempting, since the split itself is ADR-0015 applied to a second source and much of a new record restates it. Rejected: this is not a change to ADR-0015's decision, it is a decision about a hazard that record did not face, and burying it in an amendment to a record about Codex's split hides it where nobody would look for it. ADR-0015's carried-forward constraint gets a pointer here instead.

## Consequences

**Good.** `crate day` — and therefore `/daily` — now lists a resumed Claude Code session under every day it touched, with no change to `wiki.py`: each card's `started:` is simply its own day. The session-long `duration_min` and the day-one-only date are fixed at the place those values are minted, for the same change and in the same shape as Codex's, so the two adapters now disagree about nothing that reaches a card. `crate day` also goes back to being one rule over `raw/sessions/*/` rather than one rule per source, which was the coherence argument for splitting rather than teaching `crate day` to match on span.

**Bad.** A card can now change after something has read it, and this record is the only place that says so. `Card.records` also changes meaning for Claude: it was the whole file's record count while the card was built from the live path, and it is now the count of that day's live records — informational either way (it is not rendered into frontmatter), but the value stored in `state.json` means something different than it did.

**Every existing Claude card re-renders once**, because its cursor value changes from the file's live leaf to its day's last live uuid. As in ADR-0015, the content is byte-identical and the path is unchanged, so this is a write and not a change.

**The tests for this adapter are now timezone-pinned module-wide** — both the adapter's own suite and the hook suite, which borrows its fixtures. Which cards a fixture yields is a function of the local day boundary, and a fixture spanning a little over an hour straddles local midnight at a handful of real UTC offsets, so a card-count assertion that passed in CI could fail on a contributor's machine. `pytestmark = pytest.mark.usefixtures("pinned_tz")` makes each module deterministic, the same reasoning that fixture already documents. Any future module that imports a session fixture inherits the obligation.

**Constraints this carries forward.** Two things the probe could not close, neither of them changed by the split. `_live_path`'s docstring describes a compaction boundary ending the walk cleanly; the corpus held no compacted transcript, so that path is **untested rather than disproved**. And issue #41 — a forked session copying a prior conversation's records into a new file, so the same work lands on two cards under two ids — is open, is not addressed here, and is not closed by a per-day split.
