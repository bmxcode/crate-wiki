# ADR-0010 · Layer 3 splits, and `crate upgrade` keeps a baseline of what it wrote

**Status:** accepted · 2026-07-24 · revises [ADR-0009](0009-engine-owned-vault-files.md)

## Context

[ADR-0009](0009-engine-owned-vault-files.md) classified `CLAUDE.md` as authored: the engine ships its template but does not own the result, and `crate upgrade` reports drift rather than merging. That was written to protect the highest-leverage file in a vault, and one deliverable later both halves of it have failed in the same week.

**The drift report is always spurious, so it reports nothing.** Upgrade compares the vault's `CLAUDE.md` against what the *current* version would render. It has no record of what was actually installed, so it cannot distinguish "you edited this" from "the template moved" — and every engine-side change to the schema therefore lights up every vault. Measured against the two real vaults rather than assumed:

| Vault | vs. what this version ships | Actually edited |
|---|---|---|
| crate-personal | differs | no — byte-identical to what its own version shipped |
| crate-work | differs | no — byte-identical to what commit `abd5b00` rendered |

Two vaults, zero local edits, both flagged. crate-personal fired three times in one day: a stale copy, then `crate fmt` shipping, then `crate extend` shipping. A warning that is right zero times out of three is one you learn to click past, and then it is not there when a real edit is at stake. This is [ADR-0004](0004-deterministic-cli.md)'s failure mode in a new place — an answer indistinguishable from a correct one — except here the engine is the one guessing.

**And the file it protects has no room for what it was protecting.** The first real convention to need a home — source pages are titled `Session · YYYY-MM-DD · <branch>`, falling back to the short session id when the branch is `main` or absent — has nowhere in a vault to live. It has been sitting in Claude Code's project memory instead: machine-local, uncommitted, invisible in Obsidian, and structurally unable to reach crate-work. The moment it lands in `CLAUDE.md` the vault has genuinely drifted, and D5, D6 and D8 each hand back a manual merge of the file nobody wants to merge.

So the protection is backwards. `CLAUDE.md` is treated as the user's because it is important, but nothing in it is actually the user's — it is the engine's own prose about how a crate vault works, identical in both vaults except for two rendered names. What is genuinely local has no file at all.

## Decision

**Split Layer 3.** The schema and the vault's own conventions become separate files with separate owners, and the merge problem dissolves rather than needing a merge.

| | File | Author | On upgrade |
|---|---|---|---|
| How a crate vault works | `CLAUDE.md`, `AGENTS.md` | the engine | overwritten |
| What *this* vault decided | `CONVENTIONS.md` | you | never touched |

`CONVENTIONS.md` is plain markdown at the vault root, not under `.crate/`, because it is the user's file and has to be visible and editable in Obsidian alongside everything else they read. `CLAUDE.md` ships a stable pointer to it — in the layers table, and in a section that says to read it now, states that conventions *refine* the schema and never override it, and says an empty one is a normal state rather than a gap to fill. `AGENTS.md` names both files, and `ingest.md` reads both before it starts, which is what makes a convention apply in a fresh session without being told.

The convention itself never ships in the engine. `ingest.md` says titles follow `CONVENTIONS.md` and deliberately does not say what they are — crate-work has no branch-per-deliverable habit, and shipping one vault's decision would impose it on the other.

**Every vault keeps a baseline of what the engine last wrote.** `.crate/baseline.json` maps each vault-relative path to a sha256 of the exact bytes the engine installed there. Upgrade then compares three ways instead of two:

| Baseline | Vault vs. shipped | Result |
|---|---|---|
| — (file absent) | — | created |
| any | same | unchanged, and re-recorded |
| matches the vault | differs | **updated** — the engine moved, the user didn't |
| differs from the vault | differs | **edited** — left alone |
| no record | differs | **unclaimed** — left alone, because the engine cannot tell |

`crate upgrade --adopt` overrides the last two rows: take the shipped files whatever they look like, and re-record. That is the one-time migration for a vault created before the baseline existed, and the way back for anyone who customised a page template and wants the shipped one again.

The baseline is **committed**, not gitignored. `.crate/state.json` is gitignored because it is a capture cursor describing what *this machine* has read; the baseline describes the vault's own content, which every clone shares. Gitignoring it would leave a fresh clone unable to answer the question it exists to answer.

`CONVENTIONS.md` is deliberately absent from the baseline, and gets a class of its own — **seeded**: the engine creates it when it is missing and never writes it again. ADR-0009's closing constraint requires every file the engine ships to be classified when it is added, and "installs once, then hands it over" is neither engine-owned nor authored-from-nothing.

## Alternatives rejected

**Keep `CLAUDE.md` authored and add only the baseline.** The smaller change, and it fixes the false positives on its own — roughly a third of the work. Rejected as a half-measure that costs two records. It makes the warning honest without changing any outcome: the schema is still unmergeable, the convention still has nowhere to live, and D5's schema change is still a hand-merge. Worse, its decision — *`CLAUDE.md` stays authored, compared three ways* — would be reversed one deliverable later, which is the re-derivation this record exists to prevent. The baseline is not a separable increment anyway; it is the precondition that makes the split safe, because owning `CLAUDE.md` without a record of what was installed means overwriting a genuinely-edited vault silently.

**A baseline of full file copies rather than hashes.** Stronger: it preserves the common ancestor, so a real three-way merge becomes possible later. Rejected because that merge is one ADR-0009 already rejected as disproportionate, and the split removes the last file anyone would have wanted it for. It would duplicate every engine file into every vault to enable a capability the same change makes unnecessary.

**Gitignore the baseline, like `state.json`.** Symmetrical, and it keeps `.crate/` uniformly machine-local. Rejected on what a second machine sees: cloning crate-personal would produce a vault with no baseline, so every engine file is `unclaimed` and the whole mechanism is off. The baseline describes committed content and has to travel with it — the same reasoning that put the ingest ledger in `sources:` frontmatter rather than a state file ([ADR-0008](0008-code-and-prompt-inside-an-operation.md)).

**Compare mtimes instead of content.** No new file at all. Rejected: `git checkout` rewrites every mtime in a vault, so a fresh clone would report as entirely edited, and the failure would arrive on a machine where nothing was wrong. Content hashing is also the only version of this that survives CI's `CRATE_TEST_CLOCK=2027-03-01` run without anyone thinking about it.

**Put the convention in a skill.** Skills are model-invoked by description match, so a convention would load *sometimes* — and inconsistent naming across sessions is the exact failure this exists to prevent, arriving intermittently, which is worse than arriving never. A skill is also invisible to Codex, which the vault already ships `AGENTS.md` for.

**Ship the convention in the engine's `ingest.md`.** No new vault file, and it would work today. Rejected: it forces one vault's habit on the other. crate-work's sessions do not sit on deliverable branches, so the branch slot would be noise there, and the engine would be holding a decision that belongs to one vault — which is vault content in the engine, the thing [ADR-0003](0003-engine-vaults-over-fork.md) exists to keep out.

**Overwrite `CLAUDE.md` unconditionally and rely on the vault being a git repo.** Simplest possible version of the split, with `git diff` as the safety net. Rejected: it makes the safe path depend on the user having committed, which `crate upgrade` never checks, and crate-work has no remote to recover from. The recoverable failure is the one where nothing was overwritten — the same reason `raw/` is immutable.

## Consequences

**Good.** A convention added to a vault survives every upgrade, and there is one obvious place to put it. `CLAUDE.md` becomes shippable like any other engine file, so D5, D6 and D8 can change the schema freely — which is the constraint that forced this deliverable ahead of D5. The drift warning now fires only when something is genuinely wrong, so it is worth reading. And ADR-0009's unresolved "Bad" closes on the way past: a customised `.crate/templates/` is no longer silently overwritten, because the baseline notices, which was the tension that record documented rather than solved.

**Bad.** *Every* vault that exists today needs `crate upgrade --adopt` once, including the one whose `CLAUDE.md` was byte-identical to what shipped. The deliverable that introduces the baseline is also the one that changes the schema, so at the moment of migration no existing vault matches either the record (there isn't one) or the shipped file (it just moved). The engine cannot tell that from a real edit, and refusing is the whole point — but it does mean the fix for a false positive arrives as one more thing to click through, once, on every vault. Verifying that the click is safe took reconstructing both vaults' files from git history, which is exactly the work no user will do. `.crate/baseline.json` is a new committed file that will appear in vault diffs on every upgrade, which is noise in the log of a repository whose point is content. And `--adopt` is a footgun by construction: it exists to overwrite files the engine was not sure about, so the one command that fixes the migration is also the one that discards real edits.

**Constraint this imposes.** Every file the engine puts in a vault is classified **engine-owned**, **seeded** or **authored** at the time it is added — ADR-0009's constraint, now with three boxes instead of two. Engine-owned means it goes through `engine_files()` and the baseline records it; seeded means `upgrade` creates it when missing and never again; authored means the engine writes it once at `init` or not at all. And the schema is now the engine's prose: anything a single vault decides for itself goes in `CONVENTIONS.md`, so a rule that would have been added to `CLAUDE.md` for one vault's benefit has to be either generalised for every vault or written down in the vault that wants it.
