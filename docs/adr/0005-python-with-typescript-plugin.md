# ADR-0005 · Python for the engine; TypeScript confined to the Obsidian plugin

**Status:** accepted · 2026-07-17

## Context

The engine needs one language. Two candidates had real cases.

**Python** is the native language of the ecosystem this project grows into. Local model work on a Mac means MLX or llama.cpp, and both are Python-first. `uv tool install` is a clean distribution story. It's also the lingua franca of the AI space generally.

**TypeScript** has one argument that Python cannot answer: the Obsidian plugin API is TypeScript-only. The vaults *are* Obsidian vaults, so a plugin is a plausible future — and if it's ever built, some TypeScript exists regardless of what the engine is written in.

**Go** was considered for the single-binary install, and dropped quickly: it's the worst fit of the three for the embedding and local-model work in the roadmap, and install was never the bottleneck.

So the real question isn't "which language" — it's whether one language must win, or whether each surface takes the one it needs.

## Decision

**Python** for the engine: CLI, parsers, linter, MCP server. Everything with logic in it.

**TypeScript** only if the Obsidian plugin gets built, and then only as a thin client that shells out to the `crate` CLI. No logic lives on that side.

The boundary is a rule, not a preference: **the plugin renders and triggers; the engine decides.**

## Alternatives rejected

**TypeScript for everything.** One language, and the plugin comes free. Rejected: it fights the local-LLM roadmap, where every library worth using is Python.

**Python engine, no plugin ever.** Purest, and avoids polyglot entirely. Rejected as premature — Obsidian is already in the workflow, and closing the door for tidiness costs a real feature to avoid a manageable cost.

**Go.** Single static binary. Rejected: least natural fit for the LLM ecosystem the roadmap depends on, in exchange for solving a problem (`uv tool install`) that isn't a problem.

## Consequences

**Good.** Each surface uses the language its ecosystem demands. Local model work lands naturally when it arrives. The plugin stays thin enough to delete without losing anything, since all logic sits behind the CLI.

**Bad.** Two toolchains once the plugin exists — two CI setups, two dependency stories. A polyglot repo reads as unfocused unless the split is defensible, which is a large part of why this ADR exists: a reviewer will ask "why both?" within thirty seconds, and the answer should already be written down.

**The rule this creates.** Logic added to the plugin is a bug. If the plugin needs to decide something, the decision belongs in the CLI and the plugin should call it.
