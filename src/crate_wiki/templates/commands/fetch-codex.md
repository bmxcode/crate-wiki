---
description: Sweep new Codex sessions into this vault, ahead of an /ingest.
allowed-tools: Bash(crate:*)
---

Sweep Codex sessions into this vault.

Claude Code sessions capture themselves on exit, through a Stop hook. Codex has none — its `notify` slot fires per turn rather than on session exit, and is usually already taken by something else — so nothing captures a Codex session on its own. This command is the manual substitute: run it before an `/ingest` to pull in whatever's new.

```
crate capture codex --vault .
```

That's the whole operation — free, deterministic Python, the same as what the Stop hook does for Claude Code. Report exactly what it printed: how many rollouts it scanned, how many cards it captured, and the path of each one captured.

Nothing captured? Say so, plainly, and stop — it means every Codex session since the last sweep is already in the vault.

New cards land under `raw/sessions/codex/`, same as any other session card. They don't fold themselves into the wiki — that's `/ingest`'s job — so if anything was captured, point me at `/ingest` next rather than reading the cards yourself here.
