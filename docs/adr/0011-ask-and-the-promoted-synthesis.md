# ADR-0011 · `/ask` promotes an answer to a page, and adds no CLI to do it

**Status:** accepted · 2026-07-25

## Context

[ADR-0008](0008-code-and-prompt-inside-an-operation.md) settled that the code/prompt boundary runs
per step, and closed with a constraint: every operation after `/ingest` — `/ask`, `/daily`, the
linter — "gets this table written into its own record." This is `/ask`'s.

`/ask` queries the wiki: read `index.md`, read only the pages it points at, answer. On its own that
is a read-only operation and would need no record — a prompt that reads and answers rejects no
alternative worth keeping. What makes `/ask` an operation rather than a lookup is the second half:
an answer worth keeping is **promoted** to a page under `wiki/syntheses/`, so the wiki learns from
being asked instead of spending tokens to produce an answer that evaporates into a chat. That
promotion is a write, and a write is where the per-step table earns its place.

Applying the table to `/ask`:

| Step | One right answer? |
|---|---|
| Read `index.md` as the map | — (a Read; the map is already generated) |
| Which pages the question touches | no |
| Read only those pages | — |
| The answer — prose, table, or chart | no |
| Whether the answer is worth keeping | no |
| Scaffold the synthesis page | yes |
| The prose on the page | no |
| The `summary:` line | no |
| Which wiki pages the answer drew from | yes |
| Regenerate `index.md` | yes |
| Reflow paragraphs to one line each | yes |
| The `log.md` entry | yes |

## Decision

**`/ask` ships as a slash command and adds no subcommand.** Its mechanical steps are the mechanical
steps of any wiki write, and `/ingest` already built every one of them: `crate new synthesis`
scaffolds the page, `crate extend --source` records provenance, `crate index` regenerates the
catalog, `crate fmt` reflows, `crate log ask` appends the entry. There is no mechanical step in the
table without a home, so under ADR-0008 there is nothing to add — the primitives are
operation-agnostic on purpose, and a second operation reusing them is what that design predicts, not
a gap in it.

Two points the table turns on:

**Promotion is gated — the model proposes, I decide.** `/ask` answers in chat, and only when the
answer is worth keeping does it propose a synthesis — a title, a `summary:`, the pages behind it —
and then stops, writing nothing until I approve. This is `/ingest`'s Phase-2 hard stop applied to a
different write, and it keeps `/ask` honest with [ADR-0002](0002-free-capture-paid-synthesis.md):
nothing lands in the vault without my say-so. An un-promoted `/ask` writes nothing at all — no page,
no index, no log — so the folder fills only with answers someone judged worth keeping.

**A synthesis's `sources:` is provenance, same field, different layer.** On a `wiki/sources/` page
`sources:` lists the raw files behind it — the ingest ledger. On a synthesis it lists the wiki pages
behind the answer. `crate extend` appends to either without knowing the difference, because both are
the same question: what was this built from. The two never collide, because the ledger is read only
off `wiki/sources/` pages and a wikilink normalises to a path that matches no raw file — so a
synthesis can cite ten pages and none of them looks ingested.

**Synthesis titles are declarative claims.** The title is the filename, the H1 and the
`[[wikilink]]`, so it can't hold `?` or `:`, which means it can't be the question phrased as a
question. It's the answer in a line — `Capture stays free by running on a hook` — and the verbatim
question lives in the page body, where it records what was being answered.

## Alternatives rejected

**A `crate ask` or `crate promote` subcommand.** Symmetrical with the idea of `crate ingest`, and it
would make promotion a single call. Rejected for the same reason ADR-0008 rejected `crate ingest`,
plus a new one: a `crate promote` either duplicates `crate new` and `crate extend`, or it swallows
the "is this worth keeping" judgment into Python — the exact call ADR-0002 keeps with me. The
primitives already exist; wrapping them adds surface and subtracts the discussion.

**Auto-promote every answer, or every good one.** Fewer round-trips. Rejected: promoting everything
fills `syntheses/` with throwaway lookups and discards the judgment that makes a synthesis worth
more than the chat it came from; promoting "good" answers without asking writes to the vault on the
model's say-so, which is the line ADR-0002 draws. The gate is cheap and the folder stays worth
reading.

**A `question:` frontmatter field.** Tempting, to make the question queryable. Rejected on ADR-0008's
own reasoning, pointed the other way: the question is already on the page, in the body, written for a
reader. Lifting it into frontmatter duplicates a judgment call the page already makes — the same
error as deriving the index line from the first paragraph, which 0008 rejected.

## Consequences

**Good.** The most capable operation after `/ingest` adds zero new code and zero new contract with
existing vaults — it is entirely a prompt plus a one-line fix to the synthesis template. That is the
payoff of ADR-0008's primitives being operation-agnostic: the second operation to need them pays
nothing to reuse them, and `/daily` and the linter should find the same.

**Bad.** `sources:` now means two things by layer, and nothing in the CLI enforces which — a source
page could be handed a `[[wikilink]]` or a synthesis a raw path, and `crate extend` would take
either. The isolation holds by where the ledger reads, not by validation, so a future reader of the
field has to know the layer to know what it points at.

**Constraint this carries forward.** `/daily` (D6) and the linter (D8) still owe their own tables per
ADR-0008. `/ask` sets the expectation that "no new subcommand" is a normal outcome, not a warning
sign — an operation earns CLI surface only when it has a mechanical step the existing primitives
don't already cover.
