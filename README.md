# crate-wiki

An LLM wiki that compounds what you learn and what you've done.

> **Status: early.** Milestone 1 is in progress — see the [issues](https://github.com/bmxcode/crate-wiki/issues) for the roadmap.

## The problem

Since I started building with AI assistants, my working memory of my own work collapsed from about six weeks to about three days.

The work still happens. It just stops sticking, because the context now lives in chat sessions that scroll away instead of in my head. And chat history is a poor substitute for memory: it's append-only, unstructured, and re-read from scratch every time. Nothing accumulates.

## The idea

crate-wiki implements [Andrej Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). Rather than retrieving document fragments on every query and re-deriving the same conclusions, an LLM maintains a persistent, cross-referenced wiki that grows as you feed it.

Three layers:

| Layer | Contents | Written by |
|---|---|---|
| **Raw sources** | Sessions, clips, transcripts, pastes | You and the capture hook — immutable |
| **The wiki** | Summaries, entities, concepts, syntheses | The LLM |
| **The schema** | `CLAUDE.md` / `AGENTS.md` | You — it's what makes the LLM a knowledge manager rather than a chatbot |

Three operations: **ingest** a source, **ask** the wiki a question, **lint** it for contradictions and gaps.

*On the name:* DJs call it crate-digging — going through records to find what's worth playing. The raw sources are the crate.

## Two ideas shape the design

**Code does mechanics; the LLM does judgment.** Anything with a single right answer should be code. Checking that every wikilink resolves is a `for` loop, not a reasoning task — so `crate lint` does it in Python, and the LLM is only asked the thing it's uniquely good at: *do these two pages contradict each other?* This keeps the wiki reliable rather than vibes-based. See [ADR-0004](docs/adr/0004-deterministic-cli.md).

**Capture is free; synthesis is paid.** A Stop hook parses each session to disk in pure Python at zero token cost, so capture is never the thing you forget. Synthesis only runs when you ask for it, so it never surprises your token budget. See [ADR-0002](docs/adr/0002-free-capture-paid-synthesis.md).

## How it works

```
  session ends
       │
       ▼
  Stop hook ──► crate capture ──► raw/sessions/…        free, deterministic
                                       │
                                       ▼
                               you run /ingest           paid, deliberate
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
              wiki/sources/      wiki/entities/     wiki/concepts/
                    └──────────────────┼──────────────────┘
                                       ▼
                              index.md  +  log.md
```

Then `/ask` queries the wiki, and answers worth keeping are promoted to `wiki/syntheses/` — so exploring compounds instead of evaporating into chat.

## Install

```bash
uv tool install git+https://github.com/bmxcode/crate-wiki
crate --version
```

Then scaffold a vault:

```bash
crate init ~/crate-personal --scope personal
```

The vault root is an Obsidian vault, so open it and the graph works for free. `CLAUDE.md` at its root is the schema — the file that makes an assistant maintain the wiki rather than just answer about it. Read it first; it's meant to be edited as it earns changes.

`--scope work` is the same thing for client work: no journal, and a vault that refuses to push anywhere ([ADR-0001](docs/adr/0001-local-only-work-vault.md)).

## Capture automatically

Wire capture to Claude Code's Stop hook, and every session lands in the vault on its own:

```bash
crate install-hook --vault ~/crate-personal
```

That merges a Stop hook into `~/.claude/settings.json`. It's idempotent and non-destructive — re-running updates its own entry and leaves any other Stop hooks alone; point it at a new vault to move the target. Prefer to wire it by hand? Add this instead (use the absolute path from `which crate` if your hook environment doesn't have it on `PATH`):

```json
{ "hooks": { "Stop": [ { "hooks": [
  { "type": "command", "command": "crate capture claude --vault \"$HOME/crate-personal\"" }
] } ] } }
```

What to expect:

- **Zero tokens, and it never blocks session exit.** Capture is pure Python ([ADR-0002](docs/adr/0002-free-capture-paid-synthesis.md)). If anything goes wrong — a broken vault, a missing transcript — it fails quietly and the session still ends normally.
- **One card per session.** The hook fires as the session runs and rewrites the same card in place, so a session is one file that stays current, not one per turn.
- **Failures are logged, not shown.** Every outcome, good or bad, is one line in `~/.claude/crate-capture.log` — `tail` it if a card doesn't appear.
- **One machine, one vault.** Personal and work stay isolated by living on separate machines; the work machine uses `--vault ~/crate-work`.

Capture keeps *everything* — deciding which sessions are worth folding into the wiki is a judgment call that happens later, when you run `/ingest`.

## Ingest, when you're ready

`/ingest` is the first paid operation: it reads a raw source, works out what mattered, and folds it into the wiki. It's a slash command inside the vault, so run it from a Claude Code session there.

```
/ingest                                    # take the oldest pending source
/ingest raw/sessions/claude-code/2026-07-20-abcd1234.md
```

**It stops and talks to you before it writes anything.** You get the takeaways and a numbered page plan — create this, extend that, here's a contradiction with a page you already have — and nothing is written until you reply. That step is the difference between a thinking tool and a summarizer, so it isn't optional and there's no flag to skip it.

Then it writes the pages, regenerates `index.md`, and appends one line to `log.md`.

Four things underneath it are mechanical, so they're commands rather than judgment calls ([ADR-0008](docs/adr/0008-code-and-prompt-inside-an-operation.md)) — useful on their own:

```bash
crate pending --vault .                    # raw sources not yet in the wiki
crate new concept "Session Parser" --vault .
crate extend "Session Parser" --source "[[Session · 2026-07-19]]" --vault .   # bump updated:, add the source
crate fmt --vault .                        # one line per paragraph, so Obsidian renders it right
crate index --vault .                      # regenerate index.md from page frontmatter
crate log ingest --title "Session Parser" --vault .
```

Two consequences worth knowing:

- **`index.md` is generated — don't edit it.** A page's one-line index entry lives in its own `summary:` frontmatter, and `crate index` reads it from there. Anything you type into `index.md` below the header is discarded on the next regeneration.
- **Re-running `/ingest` can't duplicate a page.** Whether a source is already ingested is read off the `sources:` frontmatter of `wiki/sources/` pages, so it's committed with the vault and travels with it. Delete a source page and its raw file becomes pending again, which is what you want.

## Upgrading a vault

Most of the engine lives in the installed package, so `uv tool upgrade crate-wiki` is usually all there is to it. Two kinds of file are exceptions — slash commands and page templates have to sit inside the vault — so a release that changes those needs one command per vault:

```bash
uv tool upgrade crate-wiki
crate upgrade ~/crate-personal
```

It refreshes only what the engine owns: `.claude/commands/` and `.crate/templates/`. Everything you author — `CLAUDE.md`, `index.md`, `log.md`, `wiki/`, `raw/`, and your `config.toml` settings — is left alone. If your `CLAUDE.md` has drifted from the schema the current version ships, it says so and lets you decide; it never merges Layer 3 for you. `--dry-run` shows what would change. See [ADR-0009](docs/adr/0009-engine-owned-vault-files.md).

## Design

- [Architecture](docs/architecture.md) — the layers, the tiers, and the data flow
- [ADRs](docs/adr/) — decisions where an alternative was genuinely rejected

## License

[MIT](LICENSE)
