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

## Design

- [Architecture](docs/architecture.md) — the layers, the tiers, and the data flow
- [ADRs](docs/adr/) — decisions where an alternative was genuinely rejected

## License

[MIT](LICENSE)
