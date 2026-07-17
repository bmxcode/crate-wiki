# ADR-0001 · The work vault is local-only, with no git remote

**Status:** accepted · 2026-07-17

## Context

crate-wiki runs in two scopes. The personal vault holds articles, transcripts, and notes on my own projects. The work vault holds AI session history from client work — which means proprietary code — plus pasted emails and Slack messages.

The original instinct was to put both in private GitHub repositories. Private repos are free, backed up, and portable across machines, which is exactly what a knowledge base wants.

The problem is that "private repo" describes an *access control setting*, not a *legal position*. Pushing employer confidential material to a personal cloud account is something many employment agreements prohibit outright, regardless of who can read it. The convenience is real; so is the exposure.

## Decision

The work vault is a local git repository with **no remote configured**. It never leaves the machine.

The personal vault keeps its private GitHub remote.

Any work→personal transfer is a manual, human-reviewed step. It is never automated.

## Alternatives rejected

**Private personal GitHub repo.** The original plan. Rejected: it puts employer data in a personal account, which is the specific thing an employment agreement is likely to forbid. Convenience is not worth the category of risk.

**Employer-provided GitHub org.** Genuinely reasonable, and keeps data inside their tenancy. Rejected for now because it makes a personal tool a work-sanctioned system, which invites review, approval, and eventual ownership questions I'd rather not open for a tool I use to think.

**Encrypted remote (age / git-crypt).** GitHub stores only ciphertext. Rejected as the wrong trade: it adds key management and forfeits grep over `raw/` to solve a problem that "don't upload it" solves for free.

## Consequences

**Good.** Zero third-party exposure of work data. The isolation between scopes is enforced by physics — separate machines — rather than by discipline. No policy conversation needed.

**Bad.** No offsite backup, and no cross-machine sync. Time Machine is the answer for now, which makes the work vault a single local copy of the thing meant to fix my memory. This is the weakest joint in the design and should be revisited once the vault holds a few months I'd hate to lose.

**Also.** Automating a work→personal bridge is now permanently off the table. That's the point: an automated bridge is an automated leak.
