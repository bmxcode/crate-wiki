# ADR-0020 · The linter earns a command, reports rather than repairs, and drops a check

**Status:** accepted · 2026-08-04

## Context

[ADR-0008](0008-code-and-prompt-inside-an-operation.md) requires every operation to write its per-step table into its own record, and named the linter as still owing one. [ADR-0011](0011-ask-and-the-promoted-synthesis.md) and [ADR-0012](0012-daily-reads-raw-and-earns-a-command.md) both close by saying the same thing. This is that table, and it is the last one.

The direction was settled long before the deliverable was reached — [ADR-0004](0004-deterministic-cli.md) uses wikilink resolution as its worked example of a single right answer, [README.md](../../README.md) already promises `crate lint` does it in Python, and the vault schema this engine ships already lists both `crate lint` and `/lint` as things that exist. What was not settled is what else belongs in the command, and issue #9's spec said so explicitly: *"to be fleshed out when reached."*

Reaching it is what changed the answer. The issue predates [ADR-0016](0016-a-rewind-re-renders-the-day-it-changed.md) and [ADR-0017](0017-staleness-is-a-content-comparison.md), and one of its four deterministic bullets — `raw/` immutability violations — no longer describes anything that is a violation. A Claude Code rewind re-renders a card that has already been read, and ADR-0016 accepted that as ordinary rather than as a fault. ADR-0017 then made `crate pending` detect exactly that, as `stale`, against a content digest recorded on the page itself. The question the bullet asked now has an owner, an answer, and a ledger.

Applying ADR-0008's table to `/lint`:

| Step | One right answer? |
|---|---|
| Which `[[wikilinks]]` don't resolve | **yes** — new: `crate lint` |
| Which pages nothing links to | **yes** — `crate lint` |
| Whether `index.md` still matches the pages on disk | **yes** — `crate lint` |
| Whether a page cites a raw source in a private section | **yes** — `crate lint` |
| Whether a page cites a raw path that isn't there | **yes** — `crate lint` |
| Whether a raw file changed after the page was written | yes — and `crate pending` already says so |
| Read the pages the findings point at | — |
| Read `index.md` as the map of what exists | — (a Read; the map is already generated) |
| Do two pages contradict each other | no |
| Has a newer source superseded a claim on a page | no |
| Is a concept referenced everywhere and has no page | no |
| Is there a named gap a web search would close | no |
| Which findings are worth acting on | no |
| Repairing any of it | **never** |

## Decision

**`/lint` ships as a slash command plus one new subcommand, `crate lint`, which reports five findings and repairs none of them.**

Five mechanical steps with no existing home is a decisive answer to ADR-0011's test, and unlike `/daily` it is not close. `crate pending` is keyed on ingest state and knows nothing about the link graph. `crate index` answers the index question by *writing* the file, which is the one thing this command may not do. Nothing anywhere resolves a wikilink.

Four points the table turns on.

**The linter reports; it never repairs.** A tool that edits pages while claiming to check them produces output nobody can act on, because the state it describes is already gone. This is why `crate index`'s existence does not cover the index question — `crate index` writes, and "is the index current" has to be answerable without changing the answer. The `/lint` prompt carries the same rule where it is enforced rather than merely asserted: its `allowed-tools` omits `Write` and `Edit` entirely, so the operation cannot write a page, a stub, or a fix.

**"Missing index entries" is a comparison, not a check, and it is reported once.** `index.md` is derived ([ADR-0008](0008-code-and-prompt-inside-an-operation.md)), so the honest question is whether `render_index` over the pages on disk equals the file, and the answer to any number of missing entries is the same single `crate index`. One finding per missing page would be a list you clear with one command — noise wearing information's clothes. Only the index's *header* is swept for dead links, because the header is the half a regeneration preserves and therefore the only half anyone authored.

**Orphan detection covers `entities`, `concepts` and `syntheses`, and nothing else.** Inbound links are counted from other `wiki/` pages only: `crate index` links every page by construction, so counting the index makes every page reachable and the check finds nothing — issue #9's own observation. A `sources/` page is reached by ledger and a `daily/` page by date, and an early vault is made almost entirely of those two. Reporting all of them on day one would be the same failure as reporting the shipped templates, arriving by a different route.

**Skipping code spans and fenced blocks is a correctness requirement, not a nicety.** The `index.md` this engine ships says a page is catalogued as `` `[[Page Name]] — one line` ``, and the vault schema illustrates linking with `` `the [[Session Parser]] drops tool output` ``. Obsidian linkifies neither, so neither is a dead link — but a bare `\[\[([^\]]+)\]\]` sweep reports both on every vault, forever, which is precisely the checker nobody keeps running. `wiki.py`'s `reflow` already knew what a fenced block was and nothing anywhere knew what a code span was; `lint.strip_code` closes that gap once. Obsidian's alias (`[[Page|shown]]`) and heading (`[[Page#Section]]`) forms are resolved to `Page` for the same reason: the schema does not forbid them, and a checker reading the whole inside of the brackets would call all of them dead.

**Findings do not change the exit code.** `crate lint` exits 0 whether it found five things or none; only a bad vault exits 1, as everywhere else in the CLI.

## Alternatives rejected

**Keep the `raw/` immutability check, in some form.** The issue asked for it, and dropping a requested check needs a defence rather than silence. Rejected because after ADR-0016 and ADR-0017 there is nothing left in it that `crate pending` does not already say, and the second answer would be the worse one. `pending` compares against `source_hash:` — the digest the page recorded at the moment it read that file — and a linter re-deriving staleness would either duplicate that ledger or, more likely, fall back to something cheaper and disagree with it the first time a card was re-rendered. Two commands answering one question, one of them wrong, is worse than one command answering it. What survives of the bullet is two questions that only *look* like it: a page citing a **private** raw section, which [ADR-0006](0006-private-sections-are-context-only.md) asks the linter for by name and which `pending` cannot see because `public_sections` filters those out before the walk begins; and a page citing a raw path that **isn't there**, which `pending` cannot see because it iterates the files that exist. Both ask what a page claims it was built from, not whether that thing has moved on.

**Exit non-zero when there are findings, so `crate lint` can gate CI or a pre-commit hook.** A real capability, and the reason most linters exist. Rejected because **findings are the normal state of a working vault**: a concept you haven't linked yet, a page written mid-ingest, an index one command behind. A gate that fires on the normal state is one you disable within a week, which is the cry-wolf failure this record spends four paragraphs avoiding elsewhere. It would also break the operation that consumes it, since `/lint`'s first act is to run the command through Bash and a non-zero exit reads there as a broken command rather than as a result. A `--strict` flag remains available and is purely additive; shipping it now would be surface for a use case nobody has asked for.

**`crate lint --fix`, for the findings that have obvious fixes.** Two of the five do: a stale index is `crate index`, and an orphan could be linked from somewhere. Rejected on the first point above, and on what "obvious" is doing in that sentence — *which* page should link the orphan is the judgment `/lint` exists to ask a model, and a fix that picks one is the linter quietly writing prose. The stale index already has a command; making the checker run it means the check can never be observed failing.

**No new subcommand, on `/ask`'s precedent.** The default outcome under ADR-0011, and worth applying rather than assuming. It fails immediately: five mechanical steps, no primitive within reach of any of them, and the prompt version answers "does every wikilink resolve" probabilistically — ADR-0004's worked example of exactly the wrong thing to ask a model.

**Report every orphan, including source and daily pages.** Fewer rules and no exemption list to defend. Rejected on what the first run of a real vault would print: a page per session card and a page per day, none of which is wrong, burying the one concept nothing links to.

## Consequences

**Good.** Three promises the repo has been making since D1 — `crate lint` in the README, in `docs/architecture.md`, and in the schema every vault carries — are now true. The checks are unit-tested, which is the payoff [ADR-0004](0004-deterministic-cli.md) predicted for keeping them out of a prompt, and issue #9's three known false positives are pinned as tests rather than rediscovered on each vault. `/lint` is the first operation that writes nothing at all, which makes it the cheapest one to run and the only one safe to run on a vault you haven't decided anything about yet. And ADR-0006's unenforceable half gets the one piece of enforcement it can have: the linter cannot tell whether a page *knows* something from a private section, but it can say that a page admits in `sources:` that it read one.

**Bad.** A sixth subcommand, and surface is a contract. The exemptions are policy compiled into code — a vault that genuinely wants its daily pages linked from somewhere has no way to ask for that, and the honest reason for the boundary is a judgment about what an early vault looks like rather than anything the data forces. Wikilink resolution is exact rather than case-insensitive, so `[[session parser]]` reports as dead where Obsidian would resolve it; that follows the schema's own premise that the filename *is* the title, and it will look like a bug the first time it fires. `strip_code` handles fenced blocks and code spans and deliberately not indented code, for `_is_structural`'s reason, so a four-space-indented block containing a `[[link]]` is still swept.

**And a check the issue asked for is not built.** That is the right call and it is still a gap in the sense that someone reading #9 against the shipped command will find a bullet with no code behind it. This record is where that goes; `crate pending` is where the answer went.

**Constraint this carries forward.** ADR-0008's obligation is now discharged for every operation that exists — `/ingest`, `/ask`, `/daily`, `/lint` — so a *new* operation inherits it, and the test that has now been applied four times in three directions (nothing, one command, five) is doing work rather than rubber-stamping. Anything that later teaches `crate lint` to write, including a `--fix` that only ever runs `crate index`, reopens this record.
