# ADR-0004 · A deterministic CLI, rather than having the LLM do everything

**Status:** accepted · 2026-07-17

## Context

An LLM wiki can plausibly be built with no code at all: a `CLAUDE.md` describing the conventions, and an agent that reads raw files, writes pages, and maintains the index by hand. Karpathy's gist describes the pattern, not an implementation, and the pattern doesn't obviously require a tool.

That version is much faster to start and has nothing to install. The question is what it costs once it's running.

Two things, it turns out.

**Cost.** Asking a model to parse JSONL means paying tokens to do format conversion — on every session, forever. The transcript is mostly `tool_result` bodies, so most of what you'd pay for is content that gets discarded.

**Reliability.** An LLM asked "does every wikilink resolve?" will usually be right. Usually is the wrong bar for a question with one correct answer, and the failure is silent — you get a confident answer and no way to tell it apart from a correct one.

The distinction that resolves both: some of this work has a single right answer, and some of it requires judgment. Those should not be done by the same thing.

## Decision

The engine is a Python CLI providing deterministic primitives the LLM calls via Bash.

| Code does | The LLM does |
|---|---|
| Parse session JSONL into cards | Decide what mattered about a session |
| Resolve wikilinks, find orphans | Spot contradictions between pages |
| Track the capture cursor | Decide which page a fact belongs on |
| Scaffold vaults, append to the log | Write the prose |

The rule: **anything with a single right answer is code.**

## Alternatives rejected

**Slash commands and shell only, no packaged CLI.** Fastest to a working wiki. Rejected: it pays tokens for format conversion forever, gives probabilistic answers to deterministic questions, and leaves nothing to show for the work.

**LLM parses raw, code only lints.** A middle position. Rejected because parsing is the single most mechanical step in the system and the most expensive to hand to a model — it's exactly the wrong half to keep.

## Consequences

**Good.** Synthesis stays affordable on a Pro plan, because the model reads compact cards rather than raw transcripts. Deterministic checks are actually deterministic, and testable — `crate lint` has unit tests; a prompt does not. The CLI is also the portfolio surface, so the engineering work is legible.

**Bad.** There's a tool to install, version, and keep working. `crate init` becomes a contract with existing vaults, so its structure can't change casually.

**The boundary needs defending.** Every future feature invites the question again, and the pull is always toward "just ask the model." The test is whether the question has one right answer. If it does, it's code.
