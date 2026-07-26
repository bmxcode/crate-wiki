# Architecture decision records

One record per decision where a real alternative was rejected. If there was no alternative, there's no ADR — these exist to capture the reasoning that would otherwise be lost, not to document every choice.

| # | Decision | Why it was contested |
|---|---|---|
| [0001](0001-local-only-work-vault.md) | The work vault is local-only, with no git remote | Isolation chosen over portability and backup |
| [0002](0002-free-capture-paid-synthesis.md) | Capture is free and automatic; synthesis is paid and deliberate | Splits one apparent pipeline into two, on cost |
| [0003](0003-engine-vaults-over-fork.md) | A public engine plus separate content vaults, not a forked template | Three repos instead of one |
| [0004](0004-deterministic-cli.md) | A deterministic CLI, rather than having the LLM do everything | The whole thing could have been prompts and no code |
| [0005](0005-python-with-typescript-plugin.md) | Python for the engine; TypeScript confined to the Obsidian plugin | Polyglot needs a defence |
| [0006](0006-private-sections-are-context-only.md) | Private raw sections are readable for context but never synthesized into the wiki | A gitignore looks like it already solved this |
| [0008](0008-code-and-prompt-inside-an-operation.md) | The code/prompt boundary runs *inside* an operation, not around it | ADR-0004 read at the component level puts all of `/ingest` in a prompt |
| [0009](0009-engine-owned-vault-files.md) | The engine owns some files inside a vault, and `crate upgrade` refreshes them | A vault turns out not to be purely content — classification revised by 0010 |
| [0010](0010-conventions-file-and-upgrade-baseline.md) | Layer 3 splits, and `crate upgrade` keeps a baseline of what it wrote | Reverses 0009 on `CLAUDE.md` one deliverable later |
| [0011](0011-ask-and-the-promoted-synthesis.md) | `/ask` promotes an answer to a page, and adds no CLI to do it | ADR-0008 mandates the record; "no new subcommand" needs a defence |
| [0012](0012-daily-reads-raw-and-earns-a-command.md) | `/daily` reads `raw/` directly, and earns one command to do it | Reverses 0011's outcome one deliverable later, on the same test |
| [0013](0013-local-session-timestamps.md) | Session cards are dated and timed in local wall-clock time | Converting in place vs. adding a separate local `date:` field |
| [0014](0014-shared-card-core-per-source-adapters.md) | A shared card core, with a thin adapter per session source | Proving the abstraction (a second source) vs. branching in one module |

0007 is deliberately unused: every work vault's `config.toml` reserves it for the record that would
have to be written to reopen [ADR-0001](0001-local-only-work-vault.md) and give that vault a remote.

New ADRs get written when decisions arise — never to fill a quota.
