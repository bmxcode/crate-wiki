# ADR-0019 · A live card is the running session's card for today

**Status:** accepted · 2026-08-03

## Context

The Stop hook rewrites a session's card in place while the session runs, so a live session's card is never final. Ingesting it writes a source page that reads as a complete account of a session that then keeps going — and the act of ingesting *extends* the session, so the card grows because it was ingested. Issue #21 has the case from crate-personal: a source page written mid-flight, the session then roughly doubled in length, and everything after that point is absent from a page that gives no sign of being partial.

[ADR-0017](0017-staleness-is-a-content-comparison.md) changed what that state looks like without changing whether it is a problem. Before D21 the card reported `ingested` and the mid-flight rewrite was invisible — issue #22's bug. It now reports `stale`, continuously and correctly, for as long as the session runs: the card really has moved on at every Stop.

That answer is true and not actionable. You cannot usefully re-ingest a session still in flight, and `crate extend` only resets a digest that is wrong again one turn later. A status you must ignore every time you see it is worse than no status, because it teaches you to ignore the same word when it is about a card that has actually converged.

`CLAUDE_CODE_SESSION_ID` names the running session, and it was re-verified before this was built rather than taken from the issue: it is still set, it is inherited by a subprocess — which is how `/ingest` reaches `crate pending --vault .` through Bash — and it still matches exactly one transcript. Issue #32 is the standing lesson about building on an unverified assumption about an external interface, and this check cost one command.

## Decision

**A raw file is `live` when it is the card the running session will write again — its own session id, and today.**

### The day is half the answer

Since [ADR-0015](0015-a-day-of-a-thread-is-a-card.md) and [ADR-0016](0016-a-rewind-re-renders-the-day-it-changed.md) one session yields one card *per local day it was active on*, all sharing a session id and differing only by date. `Card.filename()` is `<date>-<session id>.md` and `Card.state_key` is `<session id>:<date>` for exactly that reason, so the id alone identifies the **session** and not the card.

Only today's card is the one the next Stop rewrites. A Stop writes records timestamped now, and those land on today's card whatever day the session began — so an earlier day's card of the same session is finished work, complete, and safe to ingest.

**The rewind case is real and deliberately not marked.** ADR-0016 established that a rewind past midnight re-renders an earlier day's card, so an earlier day is not strictly immutable. But a rewind is occasional, has no marker in the transcript format, and issue #41 is the standing reminder not to build machinery against a shape nobody has measured — while a Stop is continuous. Marking every day of the session to cover the rewind would suppress the common case to guard the rare one. When a rewind does land on an ingested earlier day, the content digest reports that card `stale`, which is the right and actionable answer there precisely because that card has converged.

### `live` wins over `new` and `stale`, and never resurfaces `ingested`

One status per line stays the output contract. `new` and `stale` say whether you *should* fold a source in; `live` says whether you usefully *can*, and the answer is no whichever of the other two applies. Nothing is lost by the relabel — the session ends, and the next `crate pending` reports the same card as `stale` or `new` again.

An already-ingested, unchanged live card keeps `ingested` and stays hidden without `--all`. That is what keeps **mark, don't hide** true in the direction that matters: no line visible today becomes invisible, and no line is added that nobody can act on.

### The marking is code; what to do about it is prompt

"Is this raw file the card of the session I am in?" has a single right answer, so by [ADR-0004](0004-deterministic-cli.md) and [ADR-0008](0008-code-and-prompt-inside-an-operation.md) it lives in `wiki.pending` rather than as a rule in a prompt that gets forgotten silently. "Skip a live card, say why, and take the next entry" is a judgment about how to conduct `/ingest`, so it is one paragraph in `templates/commands/ingest.md` — an engine-owned vault file that travels through `crate upgrade`'s baseline ([ADR-0010](0010-conventions-file-and-upgrade-baseline.md)). `cli.py` needed nothing: it already prints `path<tab>status` for anything that isn't simply new.

`card_filename(session_id, day)` moved out of `Card.filename()` into a module function in `cards.py`, because `wiki.pending` has to compute a card's name from a session id with no `Card` in hand. Naming the rule once is the same move `wiki.DIR_FOR_TYPE` makes for the type/directory mapping: derive it, so the two can't drift apart.

### It fails open in every direction

The variable unset, blank, held by a nested context that reports an id naming no card, a plain shell, a Codex sweep, CI, or a future Claude Code release that drops it — all mark nothing and leave the output byte-for-byte what it was. The check is only ever a relabel of a line that was already going to print, so it can never be the reason a source goes un-ingested.

`CLAUDE_CODE_SESSION_ID` is a Claude Code implementation detail rather than a documented API, and that coupling is recorded here and in `_live_card`'s docstring rather than left to fail mysteriously later. One trap found while verifying and worth writing down: there is also a `CLAUDE_CODE_HOST_SESSION_ID`, holding a different value and naming no transcript. It is the wrong one to reach for.

## Alternatives rejected

**Match on the session id alone, ignoring the day.** The obvious reading, and what issue #21's own text implies — "the session parser already names cards after that id" — because the issue was written before the day split existed. Rejected: it marks *every* day of a long-running session `live`, including finished days that are complete work. A day of work that no operation will ever offer is the failure [ADR-0012](0012-daily-reads-raw-and-earns-a-command.md) and ADR-0015 both exist to prevent, and it is strictly worse than the partial page this record set out to fix — a partial page is at least visible.

**Hide live cards from `crate pending` instead of marking them.** Simpler, and it makes `/ingest` correct with no prompt change at all. Rejected on the rule the issue states outright: silently omitting a file from a list is how a source goes un-ingested and nobody notices. A card you deliberately want after the session ends should still be visible, and a list that quietly disagrees with the filesystem is the one failure a ledger cannot recover from.

**Report both, as `stale, live` or a second column.** More information, and honest about the fact that a live card genuinely is stale. Rejected: it changes the output contract every consumer already parses on a tab, to say something no reader can act on. `stale` on a live card is not a second fact — it is the same fact, worded as though you could do something about it.

**Keep `Pending.needs_work`, updated for `live`.** It was `status != "ingested"`, and adding a fourth status forces a ruling on it. Rejected, and the property deleted: nothing in the tree reads it, so answering "does a live card need work?" for a caller that does not exist is inventing a contract rather than honouring one. It is two lines if something ever wants it, and by then the caller will say what it means.

**Warn, or refuse, when the variable is unset.** It would surface a Claude Code release that renamed the variable, instead of degrading into silence. Rejected: `crate pending` is run from Codex sessions, from plain shells, and from CI, where unset is the correct and ordinary state — a warning there is noise on every invocation to catch a change that may never come. Failing open means the failure mode of this feature is that it stops helping, never that it starts blocking.

## Consequences

**Good.** `/ingest` run from inside a session no longer offers that session's own card, which was the most common route to a permanently partial source page. `stale` goes back to meaning something you can act on, so ADR-0017's signal stops being drowned by the one case that fires every turn. And an earlier day of the same session stays ingestable, so a long session does not lock up its own finished work.

**Bad.** The engine now depends on an undocumented environment variable of the tool it captures from. It is one `os.environ.get` in one function and it fails open, but it is a coupling to something nobody promised to keep.

**Only the current session is detectable.** A card belonging to a live session in another window is indistinguishable from a finished one, and this does not attempt to change that — there is no signal to read. That case keeps exactly today's behaviour, which is the partial-page hazard this record narrows rather than closes.

**A test suite that could have depended on its own environment.** `tests/test_wiki.py` runs inside Claude Code as often as in CI, where the variable is genuinely set, so the module unsets it in an autouse fixture and each test that wants the signal sets it. Same class of environment-dependent flake as `CRATE_TEST_CLOCK` and `pinned_tz`, caught before it could bite. Card filenames in those fixtures are built from `date.today()` and a synthetic id — never a literal date, and never a real session id.
