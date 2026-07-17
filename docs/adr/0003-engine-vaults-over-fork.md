# ADR-0003 · A public engine plus separate content vaults, not a forked template

**Status:** accepted · 2026-07-17

## Context

I need two wikis — work and personal — that share tooling but share no data.

The obvious approach is a template repository holding both the code and the vault structure, forked once per scope. One repo to build, one thing to understand, everything versioned together.

It breaks down on the second improvement. Once the forks have real content in them, every engine change is a manual cherry-pick into two repositories that have both drifted. The tool and the data are in the same tree, so you cannot update one without merging the other.

There's a second problem, and it's permanent. A repo that has ever held vault content cannot be made public later — the data is in the history. Since this project is also meant to be a portfolio piece, a design that forecloses publishing is a design that fails one of its two goals.

## Decision

Three repositories:

| Repo | Visibility | Contents |
|---|---|---|
| `crate-wiki` | public | Engine only — CLI, parsers, linter, MCP server, templates, docs |
| `crate-personal` | private | Content only — `raw/` + `wiki/` |
| `crate-work` | local, no remote | Content only — `raw/` + `wiki/` |

The engine installs as a tool (`uv tool install`). Vaults are created by `crate init` and contain no code.

## Alternatives rejected

**Private template, forked twice.** The original plan. Rejected: engine changes need cherry-picking into two drifted forks, and the code can never go public because the history holds data.

**Monorepo with `personal/` and `work/` directories.** Simplest of all. Rejected outright — it puts work and personal content in one tree, which is the isolation boundary this whole design exists to hold.

**Engine private until it's polished.** Same structure, delayed publication. Rejected as a decision that doesn't need making now: the flip is one click whenever the README is ready, and history is preserved either way.

## Consequences

**Good.** One `uv tool upgrade` reaches both vaults. The engine can be public because data was never in it — the two goals stop competing. The boundary is structural rather than remembered.

**Bad.** Three repos instead of one, and the engine needs real packaging discipline — versioning, an install path, a stable `crate init` contract. That's overhead a single template wouldn't have had.

**Follows from this.** The engine repo must never accumulate vault content. `raw/` is gitignored here. D11 (the self-hosted dogfood vault) is the single deliberate exception, and it publishes to `docs/wiki/` through a curated, human-reviewed diff.
