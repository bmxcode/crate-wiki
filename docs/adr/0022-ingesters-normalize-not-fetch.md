# ADR-0022 · Source ingesters normalize; the engine stays offline

**Status:** accepted · 2026-09-01

## Context

D9 ([#10](https://github.com/bmxcode/crate-wiki/issues/10)) adds `crate add` — a way to fold non-session material into a vault. The three sources the issue names are pasted messages (Slack, email, Teams), web clips (Obsidian Clipper), and YouTube transcripts. A session captures itself through a hook; these don't, so they need a deliberate intake path. The question this record settles is what that path is *allowed to do*: does `crate add youtube <url>` reach out and fetch a transcript, does `crate add url <url>` fetch and extract a page — or does `crate add` only ever normalize content the user already has?

The engine as it stands has one runtime dependency (`typer`) and does **zero network I/O**. Every command is offline and deterministic: capture reads a local JSONL, the operations read local files. That is not an accident of scope — it is the character that makes capture free, testable without a network, and safe to run on a fail-quiet hook ([ADR-0002](0002-free-capture-paid-synthesis.md)). Fetching would be the first crack in it.

Fetching is genuinely deterministic — a transcript API call has one right answer, so [ADR-0004](0004-deterministic-cli.md) would let it be code rather than a prompt. The boundary ADR-0004 draws is the wrong test here. The cost of fetching isn't judgment; it's the network: new third-party dependencies (`youtube-transcript-api`, an HTML-to-markdown extractor) that age and break, a class of runtime failure (timeouts, rate limits, markup that shifted) the rest of the engine has never had to handle, and transcript-scraping in particular that breaks whenever YouTube moves the endpoint. None of that buys capability the user lacks — it buys convenience.

And the user doesn't lack the content. **Obsidian Clipper already fetches and extracts** a page to markdown; that's what it's for. A **paste** is text in hand by definition. A **YouTube transcript** is one panel away — YouTube's own "Show transcript" control renders the whole thing as selectable text. In every case the bytes already exist on the user's side of the network; what's missing is a consistent, frontmatter'd raw source, and producing that is pure local normalization.

## Decision

**`crate add` normalizes content the user already has. It never fetches.**

Each subcommand takes text — from `--file` or stdin — and writes it into a `raw/` section as a source with the shared frontmatter shape (`source:`, `title:`, `url:`, `captured:`, plus per-kind extras). `crate add url` reads an Obsidian Clipper capture and re-frontmatters it; `crate add paste` wraps a pasted message verbatim. No subcommand opens a socket. The engine's single dependency and its offline character are preserved.

This ships `paste` and `url` now. `youtube` is deferred and, when built, follows the same rule: the user pastes the transcript from YouTube's panel and `crate add youtube` wraps it with the video's url/title/channel — a normalizer, not a fetcher.

## Alternatives rejected

**Fetch everything** — `youtube <url>` calls a transcript API, `url <url>` fetches and extracts the page. The most convenient, and it makes network failure a first-class concern across the whole feature: two-or-more new dependencies, each a maintenance surface that ages independently of crate, and a `crate add` that can now fail because a remote host was slow or changed its markup. It trades the engine's offline guarantee for keystrokes the user was going to spend in Obsidian Clipper anyway.

**Fetch YouTube only** — keep `paste`/`url` offline, but let `youtube` fetch, on the grounds that it's the one source with no existing fetcher. The narrowest version of the crack, and still a crack: it pulls a transcript-scraping dependency into a zero-dependency-beyond-`typer` engine, for the source whose fetching is the *most* fragile (YouTube moves the endpoint and the library breaks). The manual path — copy from the transcript panel, pipe it in — costs the user a few clicks and costs the engine nothing, which is the right trade for a tool whose value is that capture never fails.

## Consequences

**Good.** The engine stays offline, deterministic, and one-dependency. `crate add` is trivially testable — every case is a synthetic string in, a file out, no network to mock. The intake path can never be the reason a capture fails. The frontmatter shape is shared, so a clip, a paste, and a future YouTube source read the same way and `crate pending` treats them identically.

**Bad, and worth stating.** The user does the fetching. For a web clip that's already how Obsidian Clipper works, so it costs nothing; for a YouTube transcript it's a manual copy from the transcript panel, which is a real friction the fetch-it version wouldn't have. If that friction proves to bite in practice — if the copy step is the thing that stops transcripts getting captured — this trade reopens, and reversing it (letting crate fetch) needs its own ADR that accepts the dependency and the network-failure surface with eyes open. This record is the account of why the engine is, for now, deliberately still offline.
