# Changelog

Notable changes to `crate-wiki`. The design and its rejected alternatives live in [docs/adr/](docs/adr/); this file records what changed for someone using the tool.

## Unreleased

Nothing yet.

## 0.1.0 — 2026-08-28

First public release. The loop closes: a session captures itself for free, and four operations turn captured sessions into a wiki that gets denser as you use it.

### Capture — free, deterministic, automatic

- **Claude Code sessions capture themselves** through a Stop hook (`crate install-hook`), in pure Python at zero token cost. It fails quietly and never blocks session exit, logging every outcome to `~/.claude/crate-capture.log` ([ADR-0002](docs/adr/0002-free-capture-paid-synthesis.md)).
- **The parser discards rather than converts.** It walks Claude Code's `parentUuid` tree to the live leaf so rewinds and abandoned branches never replay as work you did, and drops `tool_result` bodies and `thinking` blocks — roughly a tenth the size, carrying nearly all the signal, which is what makes synthesis affordable.
- **Codex sessions too**, through a second adapter over a shared card core ([ADR-0014](docs/adr/0014-shared-card-core-per-source-adapters.md)). Codex has no session-exit hook, so it's swept on demand with `crate capture codex` or `/fetch-codex`.
- **A day of a session is a card** ([ADR-0015](docs/adr/0015-a-day-of-a-thread-is-a-card.md)). A thread resumed across three days is three files, each dated to its own day and carrying that day's branch, duration and tool version. A rewind re-renders the day it changed, in place ([ADR-0016](docs/adr/0016-a-rewind-re-renders-the-day-it-changed.md)).
- **Cards are dated in local wall-clock time** ([ADR-0013](docs/adr/0013-local-session-timestamps.md)), so work after midnight lands on the day you did it.
- **Each card records the day's token usage** — input, output and cache read/write kept separate, with the model. The dollar cost is deliberately left outside the engine, where the rate table can age without the card going stale ([ADR-0018](docs/adr/0018-tokens-are-captured-cost-is-external.md)).

### The four operations — paid, and only when you ask

- **`/ingest`** folds a raw source into the wiki, stopping to show you its takeaways and a numbered page plan before it writes anything. There's no flag to skip that step.
- **`/ask`** queries the wiki and promotes answers worth keeping to `wiki/syntheses/`, adding no command of its own ([ADR-0011](docs/adr/0011-ask-and-the-promoted-synthesis.md)).
- **`/daily`** writes an account of a day, reading session cards straight out of `raw/` with no ingest required ([ADR-0012](docs/adr/0012-daily-reads-raw-and-earns-a-command.md)).
- **`/lint`** reports and never repairs ([ADR-0020](docs/adr/0020-the-linter-reports-and-never-repairs.md)). `crate lint` answers the five questions with a single right answer — dead wikilinks, orphans, a drifted `index.md`, a page citing a private or missing source — and the prompt asks only the four that need judgment.

### The vault

- **`crate init --scope work|personal`** scaffolds an Obsidian vault: the schema, the tree, the templates and a git repo. A work vault has no journal and refuses to push anywhere ([ADR-0001](docs/adr/0001-local-only-work-vault.md)).
- **Layer 3 is split.** `CLAUDE.md`/`AGENTS.md` are the engine's and `crate upgrade` refreshes them; `CONVENTIONS.md` is yours and the engine never writes it again ([ADR-0010](docs/adr/0010-conventions-file-and-upgrade-baseline.md)).
- **`crate upgrade` knows what it wrote.** A baseline of content hashes tells "the template moved" apart from "you edited this", so an edited file is reported rather than clobbered. `--adopt` claims a vault that predates the baseline; `--dry-run` shows what would change.
- **Staleness is a content comparison, not a timestamp** ([ADR-0017](docs/adr/0017-staleness-is-a-content-comparison.md)) — `git checkout` rewrites every mtime in a vault, which would report a fresh clone as entirely stale.
- **Deterministic primitives** the operations call at the points where the answer is fixed: `crate pending`, `day`, `new`, `extend`, `index`, `log`, `fmt`, `lint`.
