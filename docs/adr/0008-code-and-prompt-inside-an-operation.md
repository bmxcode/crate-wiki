# ADR-0008 · The code/prompt boundary runs *inside* an operation, not around it

**Status:** accepted · 2026-07-20

## Context

[ADR-0004](0004-deterministic-cli.md) settled that anything with a single right answer is code. It
settled it at the level of *components* — the session parser is code, the linter is code, writing
prose is the model's job. Every example in it is a whole component that falls cleanly on one side.

`/ingest` is the first thing that doesn't. It is one operation the user invokes once, and it is a
sequence of seven steps that alternate between mechanical and judgment several times over:

| Step | Has one right answer? |
|---|---|
| Which raw sources aren't ingested yet | yes |
| What this source established, and what's worth keeping | no |
| Which page a fact belongs on; extend or create | no |
| Building a page file with correct frontmatter | yes |
| The prose on the page | no |
| Which pages appear in `index.md`, and under which heading | yes |
| The `log.md` entry | yes |

Read as a component, `/ingest` is obviously judgment — its *purpose* is judgment, and four of the
seven steps are mechanics only in service of that. So the natural reading of ADR-0004 puts the whole
thing in a prompt, and that is what the first draft of this deliverable did.

That reading is wrong, and the way it fails is quiet. A model asked to maintain `index.md` by hand
will usually list every page. A model asked to write `## [2026-07-20] ingest | Title` will usually
get the date right — except it doesn't reliably know today's date, and nothing in the output looks
wrong when it guesses. These are ADR-0004's own failure mode (a confident answer indistinguishable
from a correct one) appearing *inside* an operation whose surrounding steps genuinely are judgment.

The pull is specifically that mechanical steps *adjacent to* judgment feel like judgment. They are
not. Adjacency is not a property of the question being asked.

## Decision

**Apply the single-right-answer test per step, not per operation.**

An operation is a sequence. Each step gets the test on its own, and a mechanical step keeps its
answer in code no matter how much judgment surrounds it. `/ingest` therefore ships as a slash
command that calls four deterministic subcommands at the four points where the answer is fixed:

| | |
|---|---|
| `crate pending` | raw sources the wiki hasn't folded in |
| `crate new <type> "<Title>"` | a page scaffolded from the vault's template, frontmatter filled |
| `crate index` | `index.md`, regenerated from page frontmatter |
| `crate log ingest --title` | one append-only entry, in the fixed format |

Two consequences of that ruling are worth stating outright, because they change file formats:

**`index.md` is derived, not authored.** Its membership and grouping are a filesystem scan; only the
one-line summary is judgment. So the summary moves onto the page it describes, as a `summary:`
frontmatter field, and `index.md` is regenerated from those. The model writes one line in the place
it is already writing; it never edits the index.

**The ingest ledger is derived too.** "Already ingested" is the union of `sources:` across
`wiki/sources/` pages — the same trick, and the reason re-running `/ingest` can't duplicate a page.

The remaining steps stay entirely with the model, including the one that matters most: the
discussion. `/ingest` presents takeaways and a page plan and then stops, before any file is written.

## Alternatives rejected

**The whole operation is a prompt, with no new CLI surface.** The reading ADR-0004 invites, and the
fastest path to a working `/ingest`. Rejected: it answers four deterministic questions
probabilistically, and three of the four fail silently — a dropped index entry, a wrong date, a
duplicated page all look exactly like success.

**The model authors `index.md`; `crate lint` catches what it missed.** A real middle position, and
tempting because it needs no format change. Rejected on when the error surfaces: the index is wrong
between the ingest and the next lint, `/ask` reads the index to decide what to read, and a lint you
haven't run yet is not a check. Generating the index makes the failure impossible rather than
detectable.

**One `crate ingest` command doing the whole operation.** Symmetrical with `crate capture`, and it
would make the operation scriptable. Rejected outright: it puts "what mattered about this session"
in Python, which is the half ADR-0004 exists to keep out of code. It would also make the discussion
step — the thing that makes this a thinking tool rather than a summarizer — structurally impossible.

**A `summary:` field is redundant with the page's opening paragraph, so derive the index from that.**
Rejected: the first paragraph is written for someone reading the page, the index line is written for
someone scanning fifty of them. Deriving one from the other guesses at a judgment call, which is the
error this ADR is about, pointed the other way.

## Consequences

**Good.** The deterministic half of the expensive operation is unit-tested — 46 tests that a prompt
could never have had. Idempotency stops being something the model must remember and becomes a
property of the data: the ledger and the index are both functions of what's on disk, so re-running
`/ingest` is safe by construction. And `crate pending` is what keeps the operation affordable, since
the alternative to a ledger is reading the wiki to work out what's missing.

**Bad.** Four new subcommands is the most surface any deliverable has added, and each is a contract
with existing vaults. `index.md` and `log.md` gain rules a human editing the vault by hand can now
break — editing the index is no longer merely redundant, it's discarded on the next regeneration.
The `summary:` field means the shipped page templates changed, which is what forced
[ADR-0009](0009-engine-owned-vault-files.md).

**Constraint this imposes.** Every operation from here — `/ask` (D5), `/lint` (D6), `/daily` (D8) —
gets this table written into its own record: each step, and which side it falls on. A step with one
right answer may not be handed to the model on the grounds that the steps around it are judgment.
And the reverse holds with equal force: `crate` gains no subcommand for a step that requires a
judgment call, however convenient the scripting would be.
