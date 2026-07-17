# ADR-0002 · Capture is free and automatic; synthesis is paid and deliberate

**Status:** accepted · 2026-07-17

## Context

The wiki needs two different things from every coding session:

1. **Capture** — get the session out of `~/.claude/projects/` and into `raw/` before it's forgotten.
2. **Synthesis** — read it, decide what mattered, and fold it into the wiki.

These look like one pipeline, so the obvious design is one pipeline: a hook that runs on session end and does both.

But they have nothing in common where it counts. Capture is a pure function over a JSONL file — the same input always yields the same output, and no judgment is involved. Synthesis is entirely judgment.

They also fail differently. A missed capture is unrecoverable in practice: the session is still on disk, but I won't remember it existed. A deferred synthesis costs nothing — the raw source waits.

And I'm on a Claude Pro plan with a rolling usage limit. Anything expensive on an automatic trigger spends that budget on a schedule I don't control, and locks me out at a moment I didn't choose.

## Decision

Split the pipeline by cost.

**Tier 0 — capture.** Pure Python in a Stop hook. Zero tokens. Runs on every session automatically.

**Tier 1 — synthesis.** `/ingest`, `/ask`, `/daily`. Costs tokens, runs only when invoked.

Nothing that costs tokens is ever on an automatic trigger.

## Alternatives rejected

**Fully manual, both tiers.** Rejected: capture would depend on me remembering, and the sessions I forget are exactly the context I'm trying to stop losing. Automating the free half costs nothing.

**Auto-capture plus scheduled auto-synthesis.** Lowest friction, and tempting. Rejected: it spends the token budget on a cron schedule rather than on my intent, so the limit arrives while I'm mid-task rather than while I'm reviewing.

**Weekly batch for both.** Cheapest. Rejected: a week-old summary fights the exact problem — my recall is gone in about three days, so a weekly cadence synthesizes work I can no longer verify.

## Consequences

**Good.** Capture is never the thing I forget. Synthesis never surprises the budget. The expensive step always has a human at the keyboard, which is also where the discussion step in `/ingest` gets its value.

**Bad.** `raw/` accumulates unsynthesized sources between ingests, so there's a backlog to notice and clear. Some sources will never be ingested — which is acceptable, since raw is immutable and stays available forever.

**Constraint this imposes.** The capture hook must be fast and must never block session exit. It fails quietly and logs; it never raises.
