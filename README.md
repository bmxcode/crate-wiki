# crate-wiki

An LLM wiki that compounds what you learn and what you've done.

[![CI](https://github.com/bmxcode/crate-wiki/actions/workflows/ci.yml/badge.svg)](https://github.com/bmxcode/crate-wiki/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)

Since I started building with AI assistants, my working memory of my own work collapsed from about six weeks to about three days. crate-wiki fixes the part of that a tool can fix. Every Claude Code session is parsed to disk as it runs — pure Python, zero tokens, no assistant involved (Codex too, on a one-command sweep). Then, only when you ask, an LLM folds one into a cross-referenced wiki you can still read in three months.

```bash
uv tool install git+https://github.com/bmxcode/crate-wiki
crate init ~/crate-personal --scope personal    # an Obsidian vault, schema included
crate install-hook --vault ~/crate-personal     # sessions now capture themselves
```

> **Status: early.** Milestones 1 and 2 are done — capture, `/ingest`, `/ask`, `/daily`, `/lint` all work and have tests. [Milestone 3](https://github.com/bmxcode/crate-wiki/milestone/3) is open. I run it daily on my personal and work machines. The vault format can still change under `crate upgrade`, and what doesn't work yet is listed below. Every contested decision has a record in [docs/adr/](docs/adr/), including the ones later reversed.

Everything stays on your machine: capture reads local transcript files and writes local Markdown, and nothing is uploaded anywhere. The only thing that costs tokens is a synthesis you ran yourself.

## The problem

The work still happens. It just stops sticking, because the context now lives in chat sessions that scroll away instead of in my head. And chat history is a poor substitute for memory: it's append-only, unstructured, and re-read from scratch every time. Nothing accumulates.

## The idea

crate-wiki implements [Andrej Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). Rather than retrieving document fragments on every query and re-deriving the same conclusions, an LLM maintains a persistent, cross-referenced wiki that grows as you feed it.

Three layers:

| Layer | Contents | Written by |
|---|---|---|
| **1 · Raw sources** | Sessions, clips, transcripts, pastes | You and the capture hook — immutable |
| **2 · The wiki** | Summaries, entities, concepts, syntheses | The LLM |
| **3 · The schema** | `CLAUDE.md` / `AGENTS.md` | The engine — it's what makes the LLM a knowledge manager rather than a chatbot |

Layer 3 has a second file with a different owner: `CONVENTIONS.md` sits beside the schema and holds what *this* vault has decided. You write it, and the engine never touches it after creating it ([ADR-0010](docs/adr/0010-conventions-file-and-upgrade-baseline.md)).

Four operations: **ingest** a source, **ask** the wiki a question, write up a **day**, and **lint** it for contradictions and gaps.

*On the name:* DJs call it crate-digging — going through records to find what's worth playing. The raw sources are the crate.

## Why not just…

**Chat history?** It's already there, and it's already failing. Scrolling back through sessions is re-reading, not remembering: nothing is condensed, nothing is cross-referenced, and the same conclusion gets re-derived every time you go looking for it.

**RAG over your notes?** A fair alternative, and for question-answering over a fixed corpus it's the better one. The difference is what happens after the answer. Retrieval hands you fragments and re-derives the conclusion on every query; a wiki writes the conclusion down once, links it to everything it touches, and gets denser as you use it. The honest cost is maintenance — a vector index needs none, and a wiki needs `/lint` and a human who reads it.

**Obsidian by itself?** The vault *is* an Obsidian vault — that's deliberate, and the graph view works from the first `crate init`. What Obsidian doesn't do is get your sessions into it for free or keep the cross-references honest. crate-wiki is the pipe and the discipline, not a replacement.

## Two ideas shape the design

**Code does mechanics; the LLM does judgment.** Anything with a single right answer should be code. Checking that every wikilink resolves is a `for` loop, not a reasoning task — so `crate lint` does it in Python, and the LLM is only asked the thing it's uniquely good at: *do these two pages contradict each other?* This keeps the wiki reliable rather than vibes-based. See [ADR-0004](docs/adr/0004-deterministic-cli.md) and [ADR-0020](docs/adr/0020-the-linter-reports-and-never-repairs.md), which splits the linter along that line and has it report without ever repairing.

**Capture is free; synthesis is paid.** A Stop hook parses each session to disk in pure Python at zero token cost, so capture is never the thing you forget. Synthesis only runs when you ask for it, so it never surprises your token budget. Nothing that costs tokens is ever on an automatic trigger. See [ADR-0002](docs/adr/0002-free-capture-paid-synthesis.md).

## How it works

```
  a session ends
        │
        ▼
  crate capture ──────────► raw/sessions/…      free · deterministic · automatic
    Claude Code: a Stop hook fires                pure Python, zero tokens
    Codex: you run one sweep
        │
        ├── /ingest ──► wiki/sources/ · entities/ · concepts/   paid · only when you ask
        │                     │
        │                     └──► index.md + log.md
        │
        └── /daily ───► wiki/daily/2026-07-24.md
                        reads raw/ directly — no ingest required

  then, over the wiki itself:

        /ask   ──► an answer, promoted to wiki/syntheses/ when it's worth keeping
        /lint  ──► dead links, orphans, an index that's drifted — then the four
                   questions code can't answer. It reports, and never repairs.
```

`/ingest` is the one that builds the wiki. `/ask` is why you built it — answers worth keeping are promoted to `wiki/syntheses/`, so exploring compounds instead of evaporating into chat. `/daily` answers the question this started from: it reads a day's session cards straight out of `raw/` and writes an account of the day you can read in three months and have the day back. `/lint` is the one operation that writes nothing at all.

## Install, and set up a vault

Python 3.11 or newer. The quickstart above uses [uv](https://docs.astral.sh/uv/); pip works too:

```bash
pip install git+https://github.com/bmxcode/crate-wiki
```

Then scaffold a vault:

```bash
crate init ~/crate-personal --scope personal
```

The vault root is an Obsidian vault, so open it and the graph works for free. `CLAUDE.md` at its root is the schema — the file that makes an assistant maintain the wiki rather than just answer about it. Read it first, but don't edit it: it's the engine's, and `crate upgrade` replaces it. Anything this vault decides for itself goes in `CONVENTIONS.md` next to it, which the engine creates once and then never touches again ([ADR-0010](docs/adr/0010-conventions-file-and-upgrade-baseline.md)).

`--scope work` is the same thing for client work: no journal, and a vault that refuses to push anywhere ([ADR-0001](docs/adr/0001-local-only-work-vault.md)).

## Capture automatically

Wire capture to Claude Code's Stop hook, and every session lands in the vault on its own:

```bash
crate install-hook --vault ~/crate-personal
```

That merges a Stop hook into `~/.claude/settings.json`. It's idempotent and non-destructive — re-running updates its own entry and leaves any other Stop hooks alone; point it at a new vault to move the target. Prefer to wire it by hand? Add this instead (use the absolute path from `which crate` if your hook environment doesn't have it on `PATH`):

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "crate capture claude --vault \"$HOME/crate-personal\""
          }
        ]
      }
    ]
  }
}
```

What to expect:

- **Zero tokens, and it never blocks session exit.** Capture is pure Python ([ADR-0002](docs/adr/0002-free-capture-paid-synthesis.md)). If anything goes wrong — a broken vault, a missing transcript — it fails quietly and the session still ends normally.
- **A day of a session is a card.** The hook fires as the session runs and rewrites the same card in place, so a session is one file that stays current, not one per turn — and a session you resume across three days is three cards, each dated to its own day and carrying that day's branch, duration and token usage ([ADR-0015](docs/adr/0015-a-day-of-a-thread-is-a-card.md), [ADR-0018](docs/adr/0018-tokens-are-captured-cost-is-external.md)).
- **Failures are logged, not shown.** Every outcome, good or bad, is one line in `~/.claude/crate-capture.log` — `tail` it if a card doesn't appear.
- **One machine, one vault.** Personal and work stay isolated by living on separate machines; the work machine uses `--vault ~/crate-work`.

Capture keeps *everything* — deciding which sessions are worth folding into the wiki is a judgment call that happens later, when you run `/ingest`.

**Codex** works too, and produces the same cards from the same core ([ADR-0014](docs/adr/0014-shared-card-core-per-source-adapters.md)). It has no Stop hook to hang capture on — its `notify` slot fires per turn rather than on session exit, and is usually already taken — so it's swept on demand instead, either from the shell or with `/fetch-codex` inside the vault:

```bash
crate capture codex --vault ~/crate-personal
```

That walks `~/.codex/sessions/` and captures every new or changed rollout in one idempotent pass.

## Ingest, when you're ready

`/ingest` is the first paid operation: it reads a raw source, works out what mattered, and folds it into the wiki. It's a slash command inside the vault, so run it from a Claude Code session there.

```
/ingest                                    # take the oldest pending source
/ingest raw/sessions/claude-code/2026-07-20-abcd1234.md
```

**It stops and talks to you before it writes anything.** You get the takeaways and a numbered page plan — create this, extend that, here's a contradiction with a page you already have — and nothing is written until you reply. That step is the difference between a thinking tool and a summarizer, so it isn't optional and there's no flag to skip it.

Then it writes the pages, regenerates `index.md`, and appends one line to `log.md`.

The mechanical steps underneath it are commands rather than judgment calls ([ADR-0008](docs/adr/0008-code-and-prompt-inside-an-operation.md)) — useful on their own:

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

## The commands

| Command | What it does |
|---|---|
| `crate init <path> --scope work\|personal` | Scaffold a vault: the schema, the tree, and a git repo to hold them |
| `crate install-hook --vault <path>` | Wire `crate capture claude` into `~/.claude/settings.json` |
| `crate upgrade <path>` | Refresh the engine-owned files in an existing vault |
| `crate capture claude` | Capture the current Claude Code session — what the Stop hook calls |
| `crate capture codex` | Sweep `~/.codex/sessions/` for new or changed rollouts |
| `crate pending` | Raw sources the wiki hasn't folded in yet, and ones that have gone stale |
| `crate day [yesterday]` | One day's session cards, oldest first — what `/daily` reads |
| `crate new <type> <title>` | Scaffold a wiki page from the vault's template |
| `crate extend <title>` | Record that a page absorbed new material: bump `updated:`, add to `sources:` |
| `crate index` | Regenerate `index.md` from every page's `summary:` frontmatter |
| `crate log <op> --title <t>` | Append one entry to `log.md` |
| `crate fmt` | Put every page's paragraphs back on one line each |
| `crate lint` | The five checks with a single right answer; prints nothing when clean |

Everything that operates on a vault takes `--vault`, defaulting to the current directory. `crate <command> --help` is the reference — each one carries the reasoning for why it exists at all.

## Upgrading a vault

Most of the engine lives in the installed package, so `uv tool upgrade crate-wiki` is usually all there is to it. Some files have to sit inside the vault — the schema, slash commands, page templates — so a release that changes those needs one command per vault:

```bash
uv tool upgrade crate-wiki
crate upgrade ~/crate-personal
```

It refreshes what the engine owns: `CLAUDE.md`, `AGENTS.md`, `.claude/commands/` and `.crate/templates/`. Everything you author — `CONVENTIONS.md`, `index.md`, `log.md`, `wiki/`, `raw/`, and your `config.toml` settings — is left alone. `--dry-run` shows what would change.

Overwriting the schema is safe because the vault records a hash of what the engine last wrote it, in `.crate/baseline.json`. That's what separates "the template moved" from "you edited this" — a file you've changed is reported and left alone rather than clobbered, and so is one the engine has no record of writing. `crate upgrade --adopt` takes the shipped versions anyway, which is the one-time step for a vault created before that record existed. See [ADR-0010](docs/adr/0010-conventions-file-and-upgrade-baseline.md).

## What doesn't work yet

- **Only sessions are ingested.** `raw/` scaffolds `clips/`, `youtube/` and `pastes/`, and nothing fills them — the Obsidian Clipper target, YouTube transcripts and pasted messages need normalising into raw sources first ([#10](https://github.com/bmxcode/crate-wiki/issues/10)).
- **There's no MCP search server.** [docs/architecture.md](docs/architecture.md) names one as part of the engine; it isn't built ([#11](https://github.com/bmxcode/crate-wiki/issues/11)). Today the wiki is searched by the assistant reading `index.md` and opening the pages it can name a reason for.
- **Codex captures only when you ask it to.** Its `notify` slot fires per turn rather than on session exit, and is usually already taken, so there's nothing to hang an automatic hook on. `crate capture codex` or `/fetch-codex` before an `/ingest` is the substitute — and forgetting is the failure mode.
- **A forked Claude Code session becomes two cards.** `claude --fork-session` copies the prior conversation verbatim into a new transcript under a new session id, so the shared prefix cards twice (and a chain of forks, once per link). This is by design: Claude Code models a fork as two sessions, nothing in the file links them, and detecting the copy would need the cross-file glob #32 rejected — so crate reflects the two sessions as two cards rather than second-guessing a deliberate fork ([ADR-0021](docs/adr/0021-a-forked-session-is-two-cards.md), closing [#41](https://github.com/bmxcode/crate-wiki/issues/41)). Plain resume and continue append in place and are unaffected.
- **Capture never tells you it failed.** That's the contract — it must not block session exit ([ADR-0002](docs/adr/0002-free-capture-paid-synthesis.md)) — but it means a missing card is silent until you look at the log.
- **It has only ever run at one person's scale.** Nothing here has met a large vault, a shared vault, or a corpus built by someone whose conventions differ from mine.

## If something doesn't work

**A session card didn't appear.** Capture fails quietly by design, so the reason is in the log and nowhere else:

```bash
tail ~/.claude/crate-capture.log
```

**The wiki looks wrong.** Run the mechanical checks before reasoning about it — they're free and they're exhaustive for the questions they answer:

```bash
crate lint --vault .        # dead links, orphans, a drifted index, private or missing sources
crate pending --vault .     # raw sources not folded in, and pages their source has outrun
```

**Something disagrees with the docs.** That's a real bug and worth an issue on its own. The README and `--help` make promises the same way a wiki page does, and promises rot.

When you file one, **don't paste session transcripts, wiki pages, or anything from a work context** — this repo is public and its history is permanent. Describe the shape of the problem, or build a synthetic file that reproduces it.

## Design

- [Architecture](docs/architecture.md) — the layers, the tiers, and the data flow
- [ADRs](docs/adr/) — one record per decision where a real alternative was rejected

The ADRs are the honest part of this repo. Each one names the alternative that lost and why, and some of them record the engine changing its mind: [ADR-0010](docs/adr/0010-conventions-file-and-upgrade-baseline.md) reverses [ADR-0009](docs/adr/0009-engine-owned-vault-files.md) one deliverable after it was accepted, and [ADR-0012](docs/adr/0012-daily-reads-raw-and-earns-a-command.md) reverses the outcome of [ADR-0011](docs/adr/0011-ask-and-the-promoted-synthesis.md) on the same test. If you want to know whether the design holds up, read those rather than this file.

## Contributing

I'm not taking pull requests yet, but issues are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Security reports go through [SECURITY.md](SECURITY.md).

## Changelog

[CHANGELOG.md](CHANGELOG.md) records what changed between releases and why.

## License

[MIT](LICENSE)
