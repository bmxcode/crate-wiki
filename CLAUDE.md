# crate-wiki — working notes

An LLM wiki that compounds what you learn and what you've done. [README.md](README.md) has the problem and the idea; [docs/architecture.md](docs/architecture.md) has the shape.

> **This file is about building the engine.** Don't confuse it with the vault `CLAUDE.md` that the engine *ships as a template* — that one is Layer 3 of a wiki, lives in a vault, and tells an LLM how to maintain that wiki. This one tells you how to work in this repo.

## Read before changing anything

- [docs/adr/](docs/adr/) — every decision already made, each with the alternative that was rejected. **Don't relitigate them.** If one looks wrong, say so — don't quietly work around it.
- [docs/architecture.md](docs/architecture.md) — the three layers and the two cost tiers.

## Rules

**This repo goes public, and git history is exposed retroactively.** Never commit real session data, client names, internal paths, or anything from a work context. Test fixtures are synthetic. The temptation is to copy a real session out of `~/.claude/projects/` to test the parser — don't. One such commit is permanent and surfaces the day the repo flips.

**Anything with a single right answer is code, not a prompt.** Resolving wikilinks is a `for` loop; spotting contradictions between pages is judgment. The first belongs in the CLI, the second in a slash command. This boundary *is* the project ([ADR-0004](docs/adr/0004-deterministic-cli.md)), and every new feature invites you to blur it — the pull is always toward "just ask the model."

**Nothing that costs tokens runs automatically** ([ADR-0002](docs/adr/0002-free-capture-paid-synthesis.md)). Capture is free Python on a hook. Synthesis runs only when invoked. The capture hook must never block session exit: fail quietly, log, never raise.

**The engine holds no vault content.** Vaults live in their own repositories; `raw/` is gitignored here. D11 is the single, curated exception ([ADR-0003](docs/adr/0003-engine-vaults-over-fork.md)).

## Working

One deliverable per session. Each has an issue — `gh issue list`, where issue #N is deliverable D(N-1). The issue is the spec.

Branch per deliverable (`d2-session-parser`), PR body says `Closes #<issue>`. Don't push to `main`.

## Verify

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv tool install --editable . && crate --version
```

CI runs all of these, plus a check that the *installed* command works — `uv run` passing is not the same as the tool working, and that gap is where a broken entrypoint hides.

System Python here is 3.9; the project needs ≥3.11. Always go through `uv`, never bare `python3`.

## Layout

```
src/crate_wiki/       engine — CLI, parsers, linter, MCP server
tests/                synthetic fixtures only
docs/architecture.md  the design
docs/adr/             one record per contested decision
```
