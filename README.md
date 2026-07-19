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

## Design

- [Architecture](docs/architecture.md) — the layers, the tiers, and the data flow
- [ADRs](docs/adr/) — decisions where an alternative was genuinely rejected

## License

[MIT](LICENSE)
