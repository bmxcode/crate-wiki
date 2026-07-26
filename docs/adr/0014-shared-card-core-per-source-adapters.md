# ADR-0014 · A shared card core, with a thin adapter per session source

**Status:** accepted · 2026-07-27

## Context

Since D2 the session parser has been one module (`session.py`) that did two separable things: it
knew Claude Code's on-disk format — a `parentUuid` tree walked to its live leaf, `isSidechain`
collapsing, the harness-injected wrappers to drop — and it owned everything downstream of a
parsed session: the `Card`/`Turn`/`Action` model, the Markdown renderer, and the idempotent
capture cursor in `.crate/state.json`. The card model was *asserted* to generalize (its docstring
promised "D7 reuses the card model with a different front-end") and the vault layout reserved
`raw/sessions/{claude-code,codex}/`, but nothing exercised the claim.

D7 (`crate capture codex`, issue #8) is the exercise. Codex's format is genuinely different — a
flat append-only log of `{timestamp, type, payload}` records with no tree, no rewinds, and no
sidechains, where a command is a `function_call` and a file edit is an `apply_patch` custom tool
call whose path lives inside a patch string, not a JSON field. If the abstraction is real, a
second source should drop in *without touching the first*. If it isn't, D7 would be the moment
that shows.

## Decision

Split the module along the line that was already there. A shared core owns the parsed `Card` and
everything after it; a per-source *adapter* owns the parse up to it.

- **`cards.py`** — the `Card`/`Turn`/`Action`/`CaptureResult` model, the renderer, the
  `state.json` cursor, and `capture(parse, session, vault_path, *, crate_version)`, which takes a
  parser and is otherwise source-agnostic. Local-wall-clock dating (ADR-0013) lives here, so
  every source inherits it.
- **`claude.py`, `codex.py`** — each a module exposing `SOURCE` and
  `parse(session, *, crate_version) -> Card | None`. The adapter owns its tree-or-log walk, its
  injected-prefix set, and its tool vocabulary; it hands back a `Card` and knows nothing about
  rendering or the cursor.

Two `Card` fields were renamed off Claude's vocabulary to make the seam honest: `cc_version` →
`tool_version` (frontmatter key too) and `last_uuid` → `cursor` — the idempotency token, whatever
a source uses for it (Claude's live-leaf uuid; Codex's record count). The cursor keys `state.json`
by source already, so the two never collide, and neither do their `raw/sessions/<source>/` dirs.

The contract between core and adapter is a **plain function** (`parse`) plus a `SOURCE` string —
duck-typed, not a class.

## Alternatives rejected

**Keep one module and branch on source** (`if source == "codex": …` inside the parse). Rejected:
it leaves the two formats entangled in one file, so the "a second source drops in without
touching the first" property — the whole thing D7 is meant to demonstrate — is exactly what you
give up. It also grows a grab-bag module that gets worse with each source.

**A formal `Adapter` ABC or `Protocol`.** The "right" OO shape, and tempting. Rejected as
premature for two implementations: a module with a `parse` function and a `SOURCE` constant is a
sufficient contract, and `capture` taking the parser as an argument is all the polymorphism this
needs. If a third source ever strains the informal contract, promoting it to a Protocol is a
mechanical change — but paying for that structure now would be abstraction ahead of evidence.

## Consequences

**Good.** The abstraction is demonstrated rather than asserted: `codex.py` adds a source and edits
no Claude code path, and the shared renderer/cursor/dating are proven to generalize (Codex got
ADR-0013's local dating for free). New sources are now a single small module against a known seam.

**Bad.** `session.py` is gone — a rename (`claude.py`) that touches imports across `hook.py`,
`cli.py`, and the tests, and dates any external reference to the old path. The two renamed `Card`
fields change the frontmatter key and the `state.json` value key; state is machine-local and
self-healing (a missing cursor just re-renders a card once), so the cost is a one-time re-render,
not lost data.

**The seam needs holding.** The temptation with each new source will be to reach back into
`cards.py` for a source-specific tweak. The test is the same as ADR-0004's: if it's true of every
source it belongs in the core, and if it's true of one it belongs in that adapter.
