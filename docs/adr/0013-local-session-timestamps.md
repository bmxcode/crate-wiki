# ADR-0013 · Session cards are dated and timed in local wall-clock time

**Status:** accepted · 2026-07-26

## Context

`crate capture` (`src/crate_wiki/session.py`) builds a session card from a Claude Code JSONL
transcript whose timestamps are raw UTC ISO-8601 strings. Everything a card says about *when* is
derived from those strings without conversion: `Card.started`/`Card.ended` (the frontmatter),
`Card.date` (`started[:10]`), `Card.filename()` (built from `date`), and every per-turn `· HH:MM`
stamp in the body (`_hhmm`, which parses and formats the raw UTC timestamp). Capture always runs
on the machine that owns the session, so the machine's local timezone at the time is the correct
wall clock to render — not UTC, and it's exactly the clock `datetime.astimezone()` (no arguments)
already knows how to reach.

[ADR-0012](0012-daily-reads-raw-and-earns-a-command.md) named this bug when it shipped `crate day`
and deliberately deferred it, closing with: *"those timestamps are UTC, so a late-evening session
in a western timezone is already filed under the next day and a daily page inherits that... If
it's worth fixing it's worth fixing where the date is minted, in the capture layer, which is a
separate change to a contract `/daily` only reads."* This is that change.

The bug is symmetric: on this machine (AEST/AEDT, UTC+10/+11) it shows up as early-morning local
work misdating to the *previous* UTC day, the mirror image of the evening-in-a-western-timezone
case ADR-0012 illustrated. Same defect, opposite direction — issue #29's framing was corrected to
match.

## Decision

**Convert in place, at one chokepoint.** A new guarded helper in `session.py`:

```python
def _local(timestamp: str) -> str:
    parsed = _parse_ts(timestamp)
    if parsed is None or parsed.tzinfo is None:
        return timestamp
    return parsed.astimezone().isoformat()
```

called from exactly two places — `parse()`, wrapping the `min`/`max` timestamp that becomes
`started`/`ended`, and `_hhmm()`, before formatting a turn's stamp. `Card.date`, `filename()`, and
`_render_card` need **no code change**: they already derive from `card.started`/`card.ended`, and
become correct the moment those values are local. `duration_min` needs no change either —
`astimezone()` changes a datetime's representation, never the instant it names, so the difference
between two converted endpoints is identical to the difference between the originals.

`_local` is guarded the same way `_parse_ts` already is: an unparseable string, or one with no
`tzinfo` to convert from, comes back unchanged rather than raising. Capture is fail-quiet by
contract ([ADR-0002](0002-free-capture-paid-synthesis.md)) — a bad timestamp degrades a card, it
never blocks the write or the session's exit.

**`wiki.day_cards`'s sort moves from lexicographic string to parsed instant.** Its current
`sorted(dated)` over `(started, path)` string tuples is safe only because UTC strings sort
chronologically for free. A local timestamp carries a UTC offset that can change across a DST
boundary (AEDT ↔ AEST), and string sort doesn't know that `"...T01:15:00+10:00"` names a *later*
instant than `"...T01:30:00+11:00"` even though it sorts first as text. `_started_key` parses each
value to an aware `datetime` for comparison, anchoring the filename-fallback bare-date case (no
time-of-day at all) to UTC midnight so it never raises comparing naive against aware.

## Alternatives rejected

**Keep `started:` as the unambiguous UTC instant; add a separate local `date:` field for
`crate day` to read.** Real, and considered seriously — it leaves the one field whose job is
precision untouched, and makes "which day" an explicit field rather than something sliced out of
a timestamp. Rejected because it fixes less while touching more: a `date:` field does nothing for
`_hhmm`, so every turn's rendered time-of-day — the worse half of the bug, since it misreports
every line in a card rather than just the header — stays wrong. It also doesn't avoid changing the
filename contract; `crate day` still ends up reading a new field instead of the one it reads
today. Converting in place fixes the header and the body from one chokepoint, and the two
unrelated-looking sites (`parse()`, `_hhmm()`) are unrelated only because they'd otherwise both
need this fix independently — a second field would still need both, plus itself.

## Consequences

**Forward-looking only, like #23.** (#23 fixed a rendering bug — reproduced markdown links
spawning Obsidian phantom notes — without touching cards already on disk, because `raw/` is
immutable in practice.) Cards already captured keep their UTC-derived filenames; nothing
renames them. A `wiki/sources/` page's `sources:` frontmatter field holds the raw card path and
*is* the ingest ledger (`wiki.ingested()`); renaming an already-ingested card's file out from under
its `sources:` entry would silently return it to pending. No migration code exists or is planned.

**A resumed pre-fix session double-writes.** `capture()`'s idempotency check compares
`prior["last_uuid"]` against the incoming leaf and requires `card_path.is_file()`, where
`card_path` is `card.filename()` under the vault's card directory. A session captured before this
fix (UTC-dated filename), resumed after it ships, computes a *new*, local-dated filename on its
next capture — the existence check misses, and `capture()` writes a second card rather than
updating the first. The original is left orphaned. This is accepted as a known, rare,
self-limiting gap, not mitigated: fixing it would mean tracking a session's filename history
independent of its content, for a one-time transition this repo only crosses once.

**Good.** A daily page (`/daily`, `crate day`) now groups a day the way a person would recognize
it, on the machine that lived it — this is the whole reason ADR-0012 flagged the deferral rather
than shipping with it silently wrong. Every downstream consumer of `Card.date`/`filename()`/the
rendered body gets the fix without being touched.

**Bad.** One more environment-dependent variable (`TZ`) now reaches the engine's output, where
before it was UTC everywhere unconditionally — a vault synced or read on a different machine than
the one that captured it would show timestamps in the *capturing* machine's zone, which is correct
for "when did this happen" but means a card's stamps aren't relative to whoever's currently reading
it. Tests that assert on a card's date/filename/turn-stamps now have to pin `TZ` explicitly rather
than inherit the test runner's, mirroring `CRATE_TEST_CLOCK`'s existing reasoning in
`tests/conftest.py`.
