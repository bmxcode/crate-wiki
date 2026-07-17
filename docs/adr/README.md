# Architecture decision records

One record per decision where a real alternative was rejected. If there was no alternative, there's no ADR — these exist to capture the reasoning that would otherwise be lost, not to document every choice.

| # | Decision | Why it was contested |
|---|---|---|
| [0001](0001-local-only-work-vault.md) | The work vault is local-only, with no git remote | Isolation chosen over portability and backup |
| [0002](0002-free-capture-paid-synthesis.md) | Capture is free and automatic; synthesis is paid and deliberate | Splits one apparent pipeline into two, on cost |
| [0003](0003-engine-vaults-over-fork.md) | A public engine plus separate content vaults, not a forked template | Three repos instead of one |
| [0004](0004-deterministic-cli.md) | A deterministic CLI, rather than having the LLM do everything | The whole thing could have been prompts and no code |
| [0005](0005-python-with-typescript-plugin.md) | Python for the engine; TypeScript confined to the Obsidian plugin | Polyglot needs a defence |

New ADRs get written when decisions arise — never to fill a quota.
