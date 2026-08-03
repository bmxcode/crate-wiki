# ADR-0018 · A card records tokens; the dollar cost is computed outside the engine

**Status:** accepted · 2026-08-03

## Context

A session costs money, and a card records everything else about a session but not that. The
motivating ask is a team on **metered API** billing that wants spend **per client**, where each
repo maps to a client (or `internal`) — so the cost of the work a card describes should be
recoverable from the card.

Two things are true and pull apart. Token counts are already in the raw formats and a per-session
sum of them is a pure function — deterministic, free, no judgment — which is exactly what Tier 0
is ([ADR-0002](0002-free-capture-paid-synthesis.md), [ADR-0004](0004-deterministic-cli.md)). A
*dollar* figure is not in the raw data at all: it is `tokens × a rate table we maintain`, and that
table ages, differs per provider, and — on a subscription plan — describes a notional API-equivalent
rather than money actually spent.

The card model also changed under this: since [ADR-0015](0015-a-day-of-a-thread-is-a-card.md) a
session is one card *per local day*, so "the session's tokens" is now "the day's tokens", and the
two sources report usage in different shapes and even different streams.

## Decision

**The engine captures token usage; it never computes dollars.** A frozen `Usage` (input, output,
cache_read, cache_write, model) hangs off `Card`, rendered as flat scalar frontmatter keys next to
`files`/`commands`. The dollar-and-client layer is a thin **external** reader over that frontmatter,
holding the team's private rates and `repo → client` map — which also keeps client names and rates
out of a repo that goes public ("the engine holds no vault content"). `cwd`/`git_branch` already
identify the repo, so the engine adds nothing else for that reader to key on.

**Categories stay separate and disjoint.** input / output / cache_read / cache_write each price
differently on the API, so they are kept apart rather than summed — and normalised so `input`
*never* includes what `cache_read` or `cache_write` counts, in **both** sources. Anthropic already
reports them disjoint; OpenAI's `input_tokens` includes both the cached and the cache-write
portions, so the Codex adapter subtracts them back out. `reasoning_output_tokens` is *inside*
`output_tokens` on OpenAI (not additive), so it is not added on top. A downstream cost is then
`input·rate_in + cache_read·rate_cached + …` with no double-counting, whichever tool produced the
session.

**Usage is attributed per day, from that day's records alone** — the invariant every other card
field already obeys ([ADR-0015](0015-a-day-of-a-thread-is-a-card.md)). For Codex this is the load-
bearing choice: its `token_count` telemetry reports a *cumulative* total, and the adapter charges
each day the **rise** in that cumulative across the day (`end − prior-day-end`, per category).
Putting the cumulative itself on each day's card would count every earlier day again on every later
day and inflate the very per-client total this exists to feed. The cumulative is read rather than
the per-turn `last_token_usage` because Codex emits `token_count` events in exact duplicate pairs,
which a per-turn sum would double — a delta of a monotonic cumulative is immune.

**Missing usage renders as absent, not zero.** `usage` is `None` for an older Claude file with no
`message.usage`, or a Codex rollout with no telemetry; such a card is byte-identical to one from
before this existed. Capture is fail-quiet ([ADR-0002](0002-free-capture-paid-synthesis.md)): an
unreadable count contributes 0, never an exception.

## Alternatives rejected

**Compute an estimated dollar cost in the engine, from a bundled rate table.** The feature as first
framed. Rejected: it moves a value that ages into engine code — the same staleness failure
[ADR-0017](0017-staleness-is-a-content-comparison.md) exists to fight, now one layer down — and on
a subscription plan the number is notional, so it would have to ship labelled "estimate" and be
recomputed whenever a price moved. Tokens are the durable, deterministic fact; the rate is the
volatile, private one, and it belongs with the team that holds it. Nothing is lost: the external
reader multiplies, and it can do so retroactively over every card ever captured.

**One combined token total per card.** Fewer keys, simpler frontmatter. Rejected: a single number
can't be priced, because a cached-read token and an output token differ by more than 10× on the
API. The split *is* the point for a cost use case.

**Sum usage across the whole session (all records), not just the day's live path.** For Claude this
would count a rewound branch's tokens — which *were* billed — that the live-path card no longer
shows. Rejected: it breaks the "every field is a function of that day's live records alone"
invariant [ADR-0015](0015-a-day-of-a-thread-is-a-card.md)/[ADR-0016](0016-a-rewind-re-renders-the-day-it-changed.md)
rest on, and there is no non-arbitrary day to charge an abandoned branch to. The accepted cost is a
small, documented undercount when a day had heavy rewinds: a card reports the tokens of the
conversation it actually shows.

## Consequences

**Good.** Cost tracking arrives without the engine ever holding a price, a client name, or a
number that goes stale — the volatile half stays external and private, the durable half is
captured once and forever. The per-category split makes an accurate metered-API cost a pure
multiply, and the same frontmatter serves Claude and Codex identically.

**Bad.** Five new frontmatter keys on every card that has usage, and a card silently gains them the
first time it's re-rendered under this engine — the capture-cursor mechanism working as designed,
but a diff to notice. A day of heavy rewinds under-reports its true billed tokens, by design.

**Both telemetry shapes were verified against a real corpus** (issue #44), the norm the other
Codex decisions (#32, #39) were held to — read on a work machine, never copied into the repo
(CLAUDE.md). The validation was not a rubber stamp: it caught a double-counting bug on *each*
source that synthetic fixtures could not have.

*Codex* (71 non-subagent rollouts): usage lives at `event_msg` → `payload.info.{total,last}_token_usage`;
the model appears *only* at `turn_context.model`; every category of the cumulative is monotonic
non-decreasing; `cached_input_tokens` and `cache_write_input_tokens` are subsets of `input_tokens`
(the latter uniformly 0 — OpenAI does not bill cache writes — but read anyway); and `total_tokens
== input_tokens + output_tokens`, i.e. `reasoning_output_tokens` is inside `output_tokens`. Two of
these corrected the first implementation — it had folded reasoning on top, and summed the per-turn
block that Codex emits in duplicate pairs — both double-counts. With the cumulative-delta read,
each day's per-category deltas telescope **exactly** to every file's final cumulative across all 50
files with telemetry.

*Claude Code* (345 sessions): the four categories are reported disjoint as assumed, but one API
response is routinely logged across **several assistant records sharing a `message.id`**, each
repeating that response's input/cache usage while output streams up. The first implementation summed
per record — which on this corpus inflated the token total by **110%** (2.10B vs the true 997M).
The fix folds usage per `message.id`, last record winning (input/cache constant across them, output
maximal at the last, verified over all 5,153 multi-record ids). Both adapters still fail to `None`
rather than guess when a field is absent.
