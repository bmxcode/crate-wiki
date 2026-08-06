# Security policy

## Supported versions

There's no tagged release yet. `main` is what's supported — if you're running crate-wiki, `uv tool upgrade crate-wiki` puts you on it.

## Reporting a vulnerability

Report it privately through GitHub: **[open a draft security advisory](https://github.com/bmxcode/crate-wiki/security/advisories/new)**. That reaches me without the report being public first.

If that form isn't available to you, open a normal issue saying only that you have a security report and how to reach you — no details in the issue — and I'll follow up privately.

I'm one person doing this alongside other work, so I can't promise a response time. I will acknowledge what I receive and say plainly whether I'm going to fix it.

## What's in scope

crate-wiki reads local AI-assistant session transcripts and writes local Markdown. The engine makes no network calls of its own and has no server and no telemetry — the one operation that reaches outward is `/lint`, which may ask *your* assistant to run a web search. So the interesting failures are all about **data staying where it should**:

- Anything that causes vault content, session transcripts, or file paths to leave the machine.
- Anything that writes content from a **private** raw section into `wiki/`, which is committed ([ADR-0006](docs/adr/0006-private-sections-are-context-only.md) is why `crate lint` checks for exactly that).
- Anything that lets a crafted transcript file cause code execution, or a write outside the vault, when it's parsed.
- Anything that breaks the isolation between a work vault and a personal one ([ADR-0001](docs/adr/0001-local-only-work-vault.md)).

**Out of scope:** the security of the AI assistants whose transcripts this reads, and anything you deliberately put in a vault and then pushed to a remote you chose.

## When you report

Please don't include real transcript content, vault pages, client names, or internal paths in the report — describe the shape of the problem, or build a synthetic file that reproduces it. This repo's history is public and permanent, and so is anything that ends up in an advisory.
