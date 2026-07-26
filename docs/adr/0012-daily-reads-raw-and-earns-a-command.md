# ADR-0012 · `/daily` reads `raw/` directly, and earns one command to do it

**Status:** accepted · 2026-07-25

## Context

[ADR-0008](0008-code-and-prompt-inside-an-operation.md) requires every operation to write down its own per-step table, and [ADR-0011](0011-ask-and-the-promoted-synthesis.md) set the expectation the table is measured against: *an operation earns CLI surface only when it has a mechanical step the existing primitives don't already cover.* `/ask` had none and added nothing. This is `/daily`'s table, and it comes out the other way.

`/daily` answers the question the project started from — *what did I do yesterday?* — by turning a day's session cards into `wiki/daily/YYYY-MM-DD.md`. Its input is "the session cards from day X", and which cards belong to a given day plainly has a single right answer. Nothing today can produce that list: `crate pending` is keyed on ingest state rather than on a date.

Applying the table to `/daily`:

| Step | One right answer? |
|---|---|
| Resolve "yesterday" to a calendar date | **yes** — new: `crate day` |
| Which session cards belong to that date | **yes** — `crate day` |
| The order the day's sessions happened in | **yes** — `crate day` |
| Read those cards | — |
| Read `index.md` as the map of what exists | — (a Read; the map is already generated) |
| What the day was about, and the thread through it | no |
| What was noise, and what was abandoned | no |
| Whether the day earns a page at all | no |
| Scaffold `wiki/daily/YYYY-MM-DD.md` | yes — `crate new daily` |
| The prose, and the `summary:` line | no |
| Which existing pages to link inline | no |
| Which cards the account was built from | yes — `crate extend --source` |
| Regenerate `index.md` | yes — `crate index` |
| Reflow paragraphs to one line each | yes — `crate fmt` |
| The `log.md` entry | yes — `crate log daily` |

## Decision

**`/daily` ships as a slash command that reads `raw/` directly, plus one new subcommand: `crate day [DATE]`.** It prints the resolved date, then one card path per line, oldest first.

Four things collapse into that one command, and each of them fails silently if the prompt does it instead:

**Resolving "yesterday" needs today's date.** This is ADR-0008's named failure mode, verbatim: a model "doesn't reliably know today's date, and nothing in the output looks wrong when it guesses." Here the output is a page *titled* for a day, so a wrong guess is indistinguishable from a right one until months later. In code it also goes through `date.today()`, which means the test clock reaches it — a shell `date -v-1d` would be unportable and invisible to `CRATE_TEST_CLOCK` both.

**`crate pending` cannot be reused, and the reason is structural.** It hides an already-ingested source, and a day's account has to read every card from that day whether or not the wiki has folded it in. Whether a session was ingested is a fact about the wiki; whether it happened on Tuesday is a fact about the session.

**The order of a day is not the order of its filenames.** A card is `<date>-<short session id>.md`, so sorting names sorts by session id. The real order is in the card's `started:` frontmatter, and a day read out of order isn't an account of anything.

**The private-section rule is enforced in code.** `_public_sections` already gates `crate pending`; a glob in a prompt would quietly not ([ADR-0006](0006-private-sections-are-context-only.md)).

Three further points the table turns on:

**`/daily` reads `raw/`, not the wiki, and is independent of `/ingest`.** "What did I do yesterday?" has to work on a day nothing has been ingested from — that's most days, since ingest is the deliberate, expensive operation. Gating the cheap question behind the expensive one would defeat the split [ADR-0002](0002-free-capture-paid-synthesis.md) exists to make. Reading raw is affordable here only because a session card is already a tenth of a transcript with nearly all the signal, which is the payoff [ADR-0004](0004-deterministic-cli.md) predicted for parsing in code. The wiki still gets read, but as a *map*: `index.md` says which pages exist, so the account can link them inline and link nothing else.

**A daily page's `sources:` is raw card paths, and it cannot pollute the ingest ledger.** `ingested()` skips every page whose `kind` isn't `sources`, and `kind` comes from the directory a page lives in — so a daily page can cite twenty cards and none of them stops being pending. That's the correct outcome, not a loophole: a day's account is not a summary of a source, and `/ingest` still owes each of those cards a source page. The field now means the same thing on all three page types — what this page was built from — expressed in the layer the page read: raw paths on a source page and a daily page, wikilinks on a synthesis.

**Days are grouped by the card's own declared date.** `started:` from the card's frontmatter, falling back to the date in its filename, which capture minted from the same field. Never mtime: a `git checkout` rewrites every mtime in a vault (the reason [ADR-0010](0010-conventions-file-and-upgrade-baseline.md) hashes content instead), and a resumed session rewrites its card days after the day it records.

The consequence to state plainly is that those timestamps are UTC, so a late-evening session in a western timezone is already filed under the next day and a daily page inherits that. The alternative — converting to local time in `/daily` — would put a card named `2026-07-25-…md` on the `2026-07-24` page, and two disagreeing notions of a card's date is worse than one that's honest and consistent. If it's worth fixing it's worth fixing where the date is minted, in the capture layer, which is a separate change to a contract `/daily` only reads. [ADR-0013](0013-local-session-timestamps.md) is that change.

## Alternatives rejected

**No new CLI: the prompt globs `raw/sessions/*/2026-07-24-*.md`.** The `/ask` precedent, and genuinely tempting, since the card filename already carries the date. Rejected on all four counts above — it guesses the date, sorts by session id, ignores the private-section rule, and quietly disagrees with `crate pending` about what a day contains. The card's frontmatter is the authority and a filename glob never reads it.

**Extend `crate pending` with a `--date` filter instead.** One fewer command, and it already walks `raw/`. Rejected: `pending` means "what the wiki hasn't folded in yet", and a day needs cards *regardless* of that. Making it answer both questions means `--date --all` becomes the real interface and the command no longer has one meaning — the ledger question and the calendar question only look alike.

**One `crate daily` command that does the whole operation.** Symmetrical with `crate capture`, and it would make the day scriptable. Rejected for the reason ADR-0008 rejected `crate ingest`: it puts "what this day was about" in Python, and it makes the Phase-2 stop structurally impossible. That stop is the whole value here — the cards record what happened, and only the person who lived the day supplies why.

**Build the daily page from that day's ingested source pages instead of from raw.** It would keep `/daily` inside `wiki/`, and its `sources:` would be wikilinks like a synthesis, which is tidier. Rejected: it answers the question only for days that have already been ingested, which inverts the cost model, and it summarises summaries — a second-hand account of a day, when the first-hand one is sitting in `raw/` and is already compact.

**Skip the discussion and just write the page.** A day is more mechanical than an ingest, so the hard stop looks like ceremony. Rejected: the cards hold prompts, prose and actions, and never *why*. The account that's worth reading in three months is the one where I corrected what the cards imply — which is impossible once the page is written.

## Consequences

**Good.** The question the project exists to answer is now one command away, on any day, whether or not anything has been ingested. `crate day` is small, unit-tested, and useful beyond `/daily` — "what happened on the 21st" is a reasonable thing to ask a vault from a terminal. And the operation writes exactly one page, so `/ingest` and `/daily` never contend for the same file: `/ingest` is forbidden from writing `wiki/daily/`, and `/daily` is forbidden from writing anything else.

**Bad.** A fifth subcommand, and the first one added since ADR-0008 — surface is a contract, and this one has to keep meaning what it means. A daily page's `created:` is the day the page was written while its title is the day it covers, which reads as a bug until you know it isn't. And "a day is its sessions": clips, pastes and YouTube carry no reliable date of their own, so a day spent reading rather than working produces a thin page. Those reach the wiki through `/ingest`, which is the honest boundary, but it does mean `wiki/daily/` is a record of sessions rather than of days.

**Constraint this carries forward.** The linter (D8) still owes its table per ADR-0008. Two operations in, the test ADR-0011 named has now been applied in both directions — `/ask` added nothing, `/daily` added one command — which is the evidence that the test is doing work rather than rubber-stamping an outcome decided in advance.
