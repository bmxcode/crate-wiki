# ADR-0015 · A day of a thread is a card, not a thread

**Status:** accepted · 2026-08-01

## Context

A Codex resume appends to the **same** rollout file, re-emitting an identical `session_meta` rather than starting a new one — the format correction that closed issue #32, now recorded in `codex.py`'s `_rollout_id`. So one rollout file is one *thread*, and a thread gets resumed many times, across days.

D7 built the Codex adapter on one-file-one-card, which was correct under the model it was written against and wrong under this one. A long-lived thread becomes a single card that is dated to the day it started (`Card.date` is `started[:10]`, and `started` is the earliest timestamp in the file), carries the branch and `cli_version` it started on (`_meta` takes the first `session_meta`), and reports a duration spanning everything since (`duration_min` is last-minus-first).

The first of those is the one that matters. `crate day` lists a card under exactly one day, so `/daily` for every later day the thread touched silently loses that work — the failure mode [ADR-0012](0012-daily-reads-raw-and-earns-a-command.md) exists to prevent, arriving by a route it did not anticipate. A card that is merely *stale* about its branch is a card you can still read; a day of work that no command will ever list is gone.

Issue #37 named two routes, and a shape-only probe of a real corpus was run before choosing. Its qualitative findings are what this record turns on — no figures from it appear here, since this repo's history is exposed retroactively and the corpus is from a work machine.

- Multi-day threads are occasional but far from negligible.
- A thread's **calendar span diverges sharply from its active days** at the tail: threads get resumed after long dormant gaps, so the longest-running thread in the corpus spanned by more than an order of magnitude more calendar days than it had days with any work on them.
- **Branch is the metadata that actually drifts.** A quarter of resumed threads changed git branch mid-thread; cwd and `cli_version` never drifted once.
- Subagent rollouts are a large share of the corpus and are already correctly excluded — a split rule must not start carding them.

## Decision

**Split a rollout by local day at capture: each day the thread was active on becomes its own card.** Three things follow from that, and a fourth is deliberately not done.

### A day is defined by activity, not by span

Records are grouped by the local calendar day of their timestamp ([ADR-0013](0013-local-session-timestamps.md) — the day boundary is the one the person who did the work lived, and `cards._local` is the chokepoint everything already goes through). A day earns a card **iff its records yield at least one turn** — the same test a whole file already had to pass, applied per day. So a dormant stretch between resumes produces nothing, and a resume whose only content is the transcript Codex replays back as a user message produces nothing either, because the injected-prefix filter already drops it.

That answers "does a day with records but no user prompt earn a card?" as **yes**. A day where the agent ran commands and edited files after a resume is work that happened that day, and losing it is the bug. Requiring a typed prompt would be a second rule answering the same question, for no gain.

Splitting happens **before** turns are built, so `_append_assistant` can never fold a Wednesday record onto a Tuesday turn, and Tuesday's card never depends on anything that came after it.

### The adapter contract returns a list

`parse(session, *, crate_version) -> list[Card]`, oldest day first, empty when nothing is usable. This changes [ADR-0014](0014-shared-card-core-per-source-adapters.md)'s seam, and `cards.capture` returns a `list[CaptureResult]` to match. `claude.py`'s diff is three lines: `None` becomes `[]` and its one card becomes a one-element list.

The asymmetry is the point. A list is the general answer to "what cards are in this file"; one source always answering *one* does not make the general shape wrong. Whether Claude Code sessions resumed across days need the same treatment is a real question this deliverable does not answer — but after this the seam is no longer what stops it.

### Each day carries the metadata in force that day, and its own cursor

A day's card takes the **last `session_meta` at or before that day's final record**. One rule, applied uniformly to cwd, branch and `cli_version`, rather than a special case for the one field the probe caught drifting — and it is the reasoning `cards._last` already documents ("metadata can shift mid-run, and the final state is the one worth recording"), scoped to a day instead of a file. It lives in `codex.py` because these are payload fields of one source's record type; `cards._last` reads top-level record keys. That is ADR-0014's seam, held.

Bounding the lookup at the day's own end — never the file's last meta — is also what makes the re-capture story work.

The cursor in `.crate/state.json` is keyed on `f"{session_id}:{date}"` (`Card.state_key`), because two cards of one thread share a session id and would otherwise overwrite each other's cursor. Its *value* is the day's own record count, not the file's, so a day's cursor moves when that day gains records and stays put when another day does. `Card.filename()` needed no change: it is already `<date>-<full session id>.md`.

Together those make an earlier day's card **stable, not merely re-rendered identically**: every field of a day's card is a function of that day's records alone, so an unchanged cursor means `cards.write` returns early and never opens the file. This matters because `raw/` is immutable to Tier 1 — a card already cited in a daily page's `sources:` must never be deleted, renamed, or rewritten out from under it. The whole design is downstream of that constraint.

### No `thread_id` / `parent_id` frontmatter

Issue #37 predicted the frontmatter drafted for #32 would become load-bearing here, since under a split it would genuinely vary. It does not, and the reason is the same one that reverted it the first time: `session_id` is already in every card's frontmatter and every day-card of a thread carries the same one, so a `thread_id` would still be a duplicate of a field that is already there. A `parent_id` pointing at the previous day's card would be genuinely new, and nothing reads it — `/daily` reads one day, and `crate day` groups on `started:`.

## Alternatives rejected

**One card per thread, and teach `crate day` to match on span** — list a card whenever the requested day falls anywhere between its `started` and `ended`. Cheaper by far, touches only `wiki.day_cards`, keeps one-file-one-card, and needs no re-capture story at all. Rejected on the probe's central finding: a thread's calendar span can exceed its active days by more than an order of magnitude, because resumes come after long dormant gaps. Matching on span would list one card under a long run of days that saw no work on it, so `/daily` for a quiet day would be handed a card mostly about other days — trading a bug that loses work for one that invents it, which is worse. It also leaves the stale branch, the stale version, and the fortnight-long `duration_min` exactly as they are.

**Keep `parse -> Card | None` and split inside `codex.capture_all`.** No change to ADR-0014's seam, and `claude.py` untouched. Rejected: `crate capture codex --transcript FILE` goes through `hook.capture_from_hook` → `cards.capture`, not through `capture_all`. Two front doors onto the same source would disagree about what a rollout is, and the fail-quiet single-file path would keep the bug.

**Split in `cards.py`, so every source inherits it.** Tempting, since ADR-0014's rule is that what's true of every source belongs in the core. Rejected because the core *cannot* do it: `Turn.time` is a rendered `"14:03"` with no date, so splitting generically would mean giving `Turn` a full timestamp purely so the core could re-derive a day the adapter already knows. That is the seam's stated failure mode in reverse — reaching into the core for something one format knows.

**An optional second adapter function the core probes for** (`parse_days` alongside `parse`). Rejected: two contracts for one seam. ADR-0014 declined even a formal Protocol as premature; a second, conditional entry point is worse than one honest signature.

## Consequences

**Good.** `crate day` — and therefore `/daily` — now lists a resumed thread under every day it touched, with no change to `wiki.py` at all: each card's `started:` is simply its own day. The stale branch, the stale CLI version and the thread-long `duration_min` are fixed by the same change rather than needing three, and each is fixed at the place the value is minted. `codex.py`'s `_meta` loses the limitation its docstring named, and `docs/architecture.md` loses the paragraph that named it.

**Bad.** Every existing card's cursor key changes, so the next capture of each re-renders it once. The content is byte-identical and the path is unchanged, so this is a write and not a change — the same one-time cost ADR-0014 accepted when it renamed `last_uuid` → `cursor`. In practice no vault crosses that transition: the only vaults that exist are throwaway test ones, cleared before the first real ingest, so this is a cost the design accepts rather than one anybody pays. And a day-two card can read as a session starting mid-thought, with no prompt to open it; `session_id` and `started:` are the whole answer to "what else is this", and they are already on the card.

**The count that `crate capture codex` prints now means something slightly different.** `scanned` and `skipped` are rollout files; `captured` and `unchanged` are cards. A thread active on three days is one rollout and three cards, so the two numbers no longer agree — which is honest rather than confusing, but it is a reporting surface that changed shape.

**Constraint this carries forward.** Claude Code sessions can also be resumed across days, and this deliverable deliberately did not look at whether their transcripts have the same problem. If they do, the fix now has a place to go.
