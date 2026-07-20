# ADR-0009 · The engine owns some files inside a vault, and `crate upgrade` refreshes them

**Status:** accepted · 2026-07-20

## Context

[ADR-0003](0003-engine-vaults-over-fork.md) promised that one `uv tool upgrade` reaches both vaults,
because the engine holds no content and the vaults hold no code. That promise held for three
deliverables, because everything the engine shipped was *behaviour* — parsing, capture, scaffolding —
and behaviour lives in the installed package.

D4 breaks it, in two places at once.

`/ingest` is a slash command. Claude Code discovers slash commands by reading
`<project>/.claude/commands/*.md` off disk, so shipping one means putting a file **inside the vault**.
There is no version of this where the engine keeps it.

And `/ingest` needs `summary:` in page frontmatter ([ADR-0008](0008-code-and-prompt-inside-an-operation.md)),
so the five page templates changed. Those were copied into `.crate/templates/` by `crate init` — at
creation time, once. crate-personal and crate-work were created before the change and will never see
it. That is precisely the drifted-forks problem ADR-0003 rejected a template repo to avoid, arriving
by a different route: not two copies of the code, but two copies of the *templates*.

So a vault is not purely content after all. Some files in it belong to the engine. The question this
record settles is which ones, and what a shipped change is allowed to do to a vault that already
exists and has real work in it.

## Decision

Classify every file the engine puts in a vault, and act only on one class.

| | Files | On upgrade |
|---|---|---|
| **Engine-owned** | `.crate/templates/*.md`, `.claude/commands/*.md` | overwritten |
| **Authored** | `CLAUDE.md`, `AGENTS.md`, `index.md`, `log.md`, `wiki/`, `raw/`, `.crate/config.toml` | never written |

`crate upgrade <vault>` rewrites the engine-owned files and reports what it did. `--dry-run` shows
the same report and writes nothing. One list in the code drives both `create` and `upgrade`, so a
file the engine ships cannot be installed by one and forgotten by the other.

`config.toml` is a narrow exception in one direction: the `crate_version` line is updated in place,
by line edit rather than re-render, because the file also holds the vault's own settings — the push
allowlist among them — and regenerating it from the preset would silently discard them.

**`CLAUDE.md` is reported, never merged.** It is Layer 3, the highest-leverage file in a vault, and
the schema explicitly tells the user to edit it as it earns changes. So the engine ships its template
but does not own the result. When the vault's copy differs from what the current version would ship,
upgrade says so and stops. Deciding what to do about it is the user's, which is the same reason
`raw/` is immutable: the recoverable failure is the one where nothing was overwritten.

## Alternatives rejected

**Manual copy, documented in the README.** No new CLI surface, and honest about how small the job is
today. Rejected: it is one command per vault per release, remembered by hand, across two vaults on
two machines — and the failure is silent, because a stale template still works, it just produces
pages missing a field. This is the drift ADR-0003 exists to prevent, and refusing to automate it
after choosing three repositories to avoid it would be choosing the cost and skipping the benefit.

**Read templates from the installed package at runtime, and copy nothing.** The strongest
alternative, and it delivers ADR-0003's promise literally: `uv tool upgrade` would reach both vaults
with no sync step at all, and there would be no such thing as a stale template. Rejected for three
reasons, in order of weight. Slash commands must be on disk regardless, so `crate upgrade` is needed
for that half anyway and the alternative buys a partial win at the cost of two mechanisms. A vault
would stop being self-describing — the templates are documentation of the page shapes, readable in
Obsidian, and moving them into a Python package hides them from the person the vault is for. And
per-vault customisation dies: a work vault whose source pages need a different shape from a personal
one currently just edits its own template, and that stops being possible.

**Overwrite everything, `CLAUDE.md` included.** The simplest rule, and it guarantees no drift. 
Rejected: it destroys the file the whole design points at. The schema is described as co-evolving and
never done; a command that discards the user's evolution of it is a command nobody would run twice.

**Version the templates and merge three ways.** Rejected as disproportionate. The engine-owned files
are the ones nobody edits, and the one file people do edit is the one we've decided not to merge —
so a merge engine would run exclusively on files where it has nothing to do.

## Consequences

**Good.** Adding a slash command, or changing a page template, is now a normal thing to ship: put the
file in `templates/`, and existing vaults get it on the next `crate upgrade`. Vaults stay
self-describing and individually customisable. The classification is enforced by tests that assert
authored files survive an upgrade with their edits intact.

**Bad.** `crate upgrade` is a step the user has to run, per vault, and nothing reminds them — a vault
can sit on old templates indefinitely and only misbehave subtly. Engine-owned files are silently
overwritten, so a user who customised `.crate/templates/` loses that on upgrade, which is exactly the
behaviour the "self-describing and customisable" argument above was defending. That tension is real
and unresolved; today it is documented rather than solved.

**Constraint this imposes.** Any new file the engine ships into a vault must be classified
engine-owned or authored *at the time it is added*, and added to the shared list rather than written
directly by `create`. An unclassified file is one that `create` installs and `upgrade` never fixes,
which is the exact bug this record exists to close.
