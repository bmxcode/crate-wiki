"""Tests for the Claude Code source adapter (`claude.py`) and `cards.capture`.

Every fixture here is synthetic, built inline — no real session ever enters this repo, because
history is exposed retroactively when it goes public (see CLAUDE.md and the D2 issue).
"""

import json

import pytest

from crate_wiki import cards, claude, vault, wiki

# Every test here runs in the pinned UTC+10 zone (`pinned_tz`, conftest.py), not the runner's.
# A session is split into one card per *local* day it was active on (ADR-0015), so which cards a
# fixture yields is now a function of the timezone: `LINEAR`'s stamps straddle local midnight at
# a handful of real offsets, and under those it would yield two cards rather than one. Pinning the
# module keeps every fixture's day count deterministic, for the same reason `pinned_tz` exists.
pytestmark = pytest.mark.usefixtures("pinned_tz")

SID = "9f3a1c2e-0000-0000-0000-000000000000"

# --------------------------------------------------------------------------------------
# a tiny builder for synthetic session trees
# --------------------------------------------------------------------------------------


def rec(uuid, parent, role, content, **extra):
    """One JSONL record. `content` is a string or a list of blocks."""
    record = {
        "uuid": uuid,
        "parentUuid": parent,
        "type": role,
        "sessionId": extra.pop("session_id", SID),
        "timestamp": extra.pop("timestamp", "2026-07-19T14:03:11.000Z"),
        "cwd": extra.pop("cwd", "/home/me/repo/crate-wiki"),
        "gitBranch": extra.pop("git_branch", "d2-session-parser"),
        "version": extra.pop("version", "1.0.83"),
        "message": {"role": role, "content": content},
    }
    record.update(extra)
    return record


def book(type_, uuid=None):
    """A bookkeeping record — a title, queue op, etc. Carries a sessionId but no parentUuid,
    the way Claude Code appends them around the real conversation."""
    return {"uuid": uuid, "type": type_, "sessionId": SID}


def use(name, **params):
    return {"type": "tool_use", "name": name, "input": params}


def result(text):
    return {"type": "tool_result", "content": text}


def write_session(tmp_path, records, name="session.jsonl"):
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def parse_days(tmp_path, records):
    """Every card a session yields — one per local day it was active on (ADR-0015)."""
    return claude.parse(write_session(tmp_path, records), crate_version="0.1.0")


def parse(tmp_path, records):
    """The single card of a session that spans one day, or None when it yields nothing.

    Most fixtures here are one day long, so they read better as one card. A fixture that spans
    days uses `parse_days` and asserts on the list.
    """
    parsed = parse_days(tmp_path, records)
    assert len(parsed) <= 1, "use parse_days for a session that spans more than one day"
    return parsed[0] if parsed else None


# `pinned_tz` lives in conftest.py now — shared with the Codex suite, and applied module-wide
# by the `pytestmark` above.


# A straightforward linear session used by several tests.
LINEAR = [
    rec("u1", None, "user", "Implement D2. Plan mode first.", timestamp="2026-07-19T14:03:00.000Z"),
    rec(
        "a1",
        "u1",
        "assistant",
        [{"type": "text", "text": "On it."}, use("Write", file_path="src/crate_wiki/session.py")],
    ),
    rec("t1", "a1", "user", [result("wrote 300 lines")]),
    rec("a2", "t1", "assistant", [use("Bash", command="uv run pytest -q")]),
    rec("t2", "a2", "user", [result("12 passed")]),
    rec(
        "a3",
        "t2",
        "assistant",
        [{"type": "text", "text": "All green."}],
        timestamp="2026-07-19T15:15:00.000Z",
    ),
]


# --------------------------------------------------------------------------------------
# what the card keeps
# --------------------------------------------------------------------------------------


def test_a_linear_session_keeps_intent_prose_files_and_commands(tmp_path):
    card = parse(tmp_path, LINEAR)

    assert card is not None
    assert "Implement D2. Plan mode first." in card.turns[0].prose
    assert card.turns[0].role == "user"
    assert card.files == ["src/crate_wiki/session.py"]
    assert card.command_count == 1

    rendered = card.render()
    assert "Implement D2. Plan mode first." in rendered
    assert "All green." in rendered
    assert "- run `uv run pytest -q`" in rendered
    assert "- edit src/crate_wiki/session.py" in rendered


def test_tool_result_bodies_are_dropped(tmp_path):
    rendered = parse(tmp_path, LINEAR).render()
    assert "wrote 300 lines" not in rendered
    assert "12 passed" not in rendered


def test_thinking_blocks_are_dropped(tmp_path):
    records = [
        rec("u1", None, "user", "Do the thing."),
        rec(
            "a1",
            "u1",
            "assistant",
            [
                {"type": "thinking", "thinking": "secret internal reasoning I should not leak"},
                {"type": "text", "text": "Done."},
            ],
        ),
    ]
    rendered = parse(tmp_path, records).render()
    assert "secret internal reasoning" not in rendered
    assert "Done." in rendered


def test_noise_tools_do_not_become_actions(tmp_path):
    records = [
        rec("u1", None, "user", "Look around."),
        rec(
            "a1",
            "u1",
            "assistant",
            [use("Read", file_path="README.md"), use("Grep", pattern="foo")],
        ),
    ]
    card = parse(tmp_path, records)
    assert card.files == []
    assert card.command_count == 0
    assert "README.md" not in card.render()


def test_consecutive_assistant_steps_fold_into_one_turn(tmp_path):
    # LINEAR has three assistant records separated by tool_result "user" records that drop out.
    card = parse(tmp_path, LINEAR)
    roles = [t.role for t in card.turns]
    assert roles == ["user", "assistant"]
    assert card.turns[1].prose.startswith("On it.")
    assert "All green." in card.turns[1].prose


def test_prose_and_actions_render_in_document_order(tmp_path):
    # A turn that reasons, acts, reasons, acts — the card must preserve that sequence, not
    # group all prose then all actions (which loses "the commit came after the tests").
    records = [
        rec("u1", None, "user", "Ship it."),
        rec(
            "a1",
            "u1",
            "assistant",
            [
                {"type": "text", "text": "First I run the tests."},
                use("Bash", command="pytest"),
                {"type": "text", "text": "Green, so I commit."},
                use("Bash", command="git commit"),
            ],
        ),
    ]
    body = parse(tmp_path, records).render()
    order = [body.index(s) for s in ("First I run", "pytest", "Green, so I commit", "git commit")]
    assert order == sorted(order), "prose and actions must stay interleaved in order"


def test_local_path_markdown_links_render_as_inert_code_spans(tmp_path):
    # A reproduced markdown link to a relative path is a live node in Obsidian's Graph view;
    # clicking it materializes a blank <path>.md phantom in the vault. Real URLs must survive.
    records = [
        rec("u1", None, "user", "See [tests/test_session.py](tests/test_session.py)."),
        rec(
            "a1",
            "u1",
            "assistant",
            [
                {"type": "text", "text": "Landed [session.py](src/crate_wiki/session.py) today."},
                {"type": "text", "text": "Rationale in [the ADR](https://example.com/adr)."},
            ],
        ),
    ]
    body = parse(tmp_path, records).render()

    # Every local-target link — in the prompt and the assistant paragraph — is neutralized to a
    # code span of its label, and no clickable local target survives.
    assert "`tests/test_session.py`" in body
    assert "`session.py`" in body
    assert "](tests/test_session.py)" not in body
    assert "](src/crate_wiki/session.py)" not in body
    # A genuine URL link stays clickable.
    assert "[the ADR](https://example.com/adr)" in body


def test_harness_injected_user_records_never_pose_as_prompts(tmp_path):
    # A task notification carries no distinguishing field — same shape as a real prompt — so
    # the wrapper text is the only signal that it wasn't typed by a human.
    injected = "<task-notification>\n<status>completed</status>\n</task-notification>"
    records = [
        rec("u1", None, "user", "Real intent here."),
        rec("a1", "u1", "assistant", [{"type": "text", "text": "Working."}]),
        rec("inj", "a1", "user", injected),
        rec("a2", "inj", "assistant", [{"type": "text", "text": "Still working."}]),
    ]
    card = parse(tmp_path, records)

    assert [t.role for t in card.turns] == ["user", "assistant"]
    assert "Real intent here." in card.turns[0].prose
    assert "task-notification" not in card.render()


# --------------------------------------------------------------------------------------
# the tree walk
# --------------------------------------------------------------------------------------


def test_a_dead_branch_from_a_rewind_is_discarded(tmp_path):
    # u1 has two children: the abandoned branch (a_dead) and the live one (a_live). The live
    # branch was appended last, so it is what the session ended on.
    records = [
        rec("u1", None, "user", "First attempt at the prompt."),
        rec("a_dead", "u1", "assistant", [{"type": "text", "text": "ABANDONED wrong path"}]),
        rec("u2", "u1", "user", "Second attempt after rewind."),
        rec("a_live", "u2", "assistant", [{"type": "text", "text": "LIVE correct path"}]),
    ]
    rendered = parse(tmp_path, records).render()
    assert "LIVE correct path" in rendered
    assert "Second attempt after rewind." in rendered
    assert "ABANDONED wrong path" not in rendered


def test_a_dangling_parent_stops_the_walk_cleanly(tmp_path):
    # a1 points at a parent that isn't in the file — a compaction boundary. The walk stops
    # there instead of crashing, and still emits the records it can reach.
    records = [
        rec("a1", "compacted-away", "assistant", [{"type": "text", "text": "after compaction"}]),
    ]
    card = parse(tmp_path, records)
    assert card is not None
    assert "after compaction" in card.render()


def test_bookkeeping_records_after_the_last_turn_do_not_become_the_leaf(tmp_path, pinned_tz):
    # Real sessions end with title/queue records appended after the conversation. They carry
    # a sessionId but no parentUuid, so the leaf must be the last real turn, not the last line.
    records = [
        *LINEAR,
        book("ai-title"),
        book("last-prompt"),
        book("custom-title"),
    ]
    card = parse(tmp_path, records)

    # LINEAR's UTC timestamps land on 2026-07-20 local under the pinned UTC+10 zone.
    assert card.date == "2026-07-20", "must not fall back to 'undated'"
    assert card.title == "d2-session-parser", "must not fall back to the short session id"
    assert [t.role for t in card.turns] == ["user", "assistant"]
    assert card.duration_min == 72
    assert "All green." in card.render()


def test_a_session_of_only_bookkeeping_parses_to_nothing(tmp_path):
    assert parse(tmp_path, [book("ai-title"), book("queue-operation")]) is None


def test_attachment_and_system_records_thread_through_the_walk(tmp_path):
    # These carry uuid/parentUuid, so they're links in the chain — the walk passes through
    # them to reach the turns, but they aren't turns themselves.
    records = [
        rec("u1", None, "user", "Start."),
        {"uuid": "att", "parentUuid": "u1", "type": "attachment", "sessionId": "x"},
        rec("a1", "att", "assistant", [{"type": "text", "text": "Reached the end."}]),
    ]
    card = parse(tmp_path, records)
    assert [t.role for t in card.turns] == ["user", "assistant"]
    assert "Reached the end." in card.render()


def test_a_cycle_does_not_hang(tmp_path):
    records = [
        rec("a", "b", "assistant", [{"type": "text", "text": "one"}]),
        rec("b", "a", "assistant", [{"type": "text", "text": "two"}]),
    ]
    card = parse(tmp_path, records)  # must terminate
    assert card is not None


# --------------------------------------------------------------------------------------
# sidechains
# --------------------------------------------------------------------------------------


def test_a_subagent_collapses_to_one_line_and_its_body_is_dropped(tmp_path):
    chatter = {"type": "text", "text": "subagent chatter"}
    records = [
        rec("u1", None, "user", "Search the codebase."),
        rec(
            "a1",
            "u1",
            "assistant",
            [use("Task", subagent_type="Explore", description="find JSONL patterns")],
        ),
        # The subagent's own turns are sidechain records — dropped, not walked.
        rec("s1", None, "user", "internal subagent prompt", isSidechain=True),
        rec("s2", "s1", "assistant", [chatter], isSidechain=True),
        rec("t1", "a1", "user", [result("subagent found 3 files")]),
        rec("a2", "t1", "assistant", [{"type": "text", "text": "Done searching."}]),
    ]
    card = parse(tmp_path, records)
    rendered = card.render()

    assert card.subagent_count == 1
    assert "- subagent (Explore)" in rendered
    assert "internal subagent prompt" not in rendered
    assert "subagent chatter" not in rendered
    assert "subagent found 3 files" not in rendered


# --------------------------------------------------------------------------------------
# metadata and the rollup
# --------------------------------------------------------------------------------------


def test_frontmatter_carries_the_deterministic_rollup(tmp_path):
    card = parse(tmp_path, LINEAR)
    rendered = card.render()

    assert card.duration_min == 72  # 14:03 → 15:15
    assert "source: claude-code" in rendered
    assert "git_branch: d2-session-parser" in rendered
    assert "tool_version: 1.0.83" in rendered
    assert "crate_version: 0.1.0" in rendered
    assert "files: [src/crate_wiki/session.py]" in rendered
    assert "commands: 1" in rendered


def test_the_title_and_filename_lead_with_branch_and_date(tmp_path, pinned_tz):
    card = parse(tmp_path, LINEAR)
    assert card.title == "d2-session-parser"
    # LINEAR's UTC timestamps land on 2026-07-20 local under the pinned UTC+10 zone.
    assert card.date == "2026-07-20"
    assert card.filename() == "2026-07-20-9f3a1c2e-0000-0000-0000-000000000000.md"


# --------------------------------------------------------------------------------------
# local time (#29) — a card is dated and timed by the machine's wall clock, not UTC
# --------------------------------------------------------------------------------------


def test_a_late_utc_session_is_dated_by_local_day_not_utc_day(tmp_path, pinned_tz):
    # 23:30-23:45 UTC is 09:30-09:45 the next day under the pinned UTC+10 zone — a session that
    # ran entirely on one local day must not be filed under the UTC day before it.
    records = [
        rec("u1", None, "user", "Late one.", timestamp="2026-07-19T23:30:00.000Z"),
        rec(
            "a1",
            "u1",
            "assistant",
            [{"type": "text", "text": "Still going."}],
            timestamp="2026-07-19T23:45:00.000Z",
        ),
    ]
    card = parse(tmp_path, records)
    assert card.date == "2026-07-20"
    assert card.filename() == "2026-07-20-9f3a1c2e-0000-0000-0000-000000000000.md"


def test_turn_timestamps_render_in_local_time_not_utc(tmp_path, pinned_tz):
    records = [rec("u1", None, "user", "Hi.", timestamp="2026-07-19T14:03:00.000Z")]
    rendered = parse(tmp_path, records).render()
    assert "· 00:03" in rendered  # 14:03 UTC + 10h
    assert "· 14:03" not in rendered


def test_an_unparseable_timestamp_does_not_crash_and_degrades_quietly(tmp_path, pinned_tz):
    records = [rec("u1", None, "user", "Odd.", timestamp="not-a-timestamp")]
    card = parse(tmp_path, records)
    assert card is not None
    assert card.started == "not-a-timestamp"  # _local's guard: unchanged, not raised
    assert card.turns[0].time == ""  # _hhmm's guard: blank, not a crash


def test_paths_inside_the_cwd_are_relativized_and_others_stay_absolute(tmp_path):
    # cwd is /home/me/repo/crate-wiki (from the rec() default). A file in the tree reads
    # relative; a plan file elsewhere keeps its absolute path rather than a ../../.. trail.
    outside = "/home/me/.claude/plans/plan.md"
    records = [
        rec("u1", None, "user", "Edit two files."),
        rec(
            "a1",
            "u1",
            "assistant",
            [
                use("Write", file_path="/home/me/repo/crate-wiki/src/crate_wiki/session.py"),
                use("Edit", file_path=outside),
            ],
        ),
    ]
    card = parse(tmp_path, records)

    assert card.files == [outside, "src/crate_wiki/session.py"]
    rendered = card.render()
    assert "- edit src/crate_wiki/session.py" in rendered
    assert f"- edit {outside}" in rendered


def test_malformed_lines_are_skipped_not_fatal(tmp_path):
    path = tmp_path / "s.jsonl"
    good = json.dumps(rec("u1", None, "user", "keep me"))
    path.write_text(good + "\n{ this is not json\n", encoding="utf-8")

    (card,) = claude.parse(path, crate_version="0.1.0")
    assert "keep me" in card.render()


def test_an_empty_session_parses_to_nothing(tmp_path):
    assert parse(tmp_path, []) is None


# --------------------------------------------------------------------------------------
# splitting a session into its days (ADR-0015) — a resume appends to the same file, so one
# file is one session that can run for days. Each local day it was active on is its own card,
# and the split runs over the live path, which a rewind can re-decide retroactively (ADR-0016).
# --------------------------------------------------------------------------------------


def at(day, hhmm="09:00"):
    """A timestamp on `day` in the pinned UTC+10 zone, so the local day is unambiguous."""
    return f"{day}T{hhmm}:00+10:00"


def day_one_two_three():
    """A session worked on across three local days, resumed into the same file each time.

    Each resume carries the branch and Claude Code version current *then* — the drift a single
    card, dated to day one, would flatten onto the day it started.
    """
    return [
        rec("u1", None, "user", "Day one work.", timestamp=at("2026-07-19", "09:00")),
        rec(
            "a1",
            "u1",
            "assistant",
            [use("Bash", command="uv run pytest -q")],
            timestamp=at("2026-07-19", "09:30"),
        ),
        # resumed the next day, on a new branch and a newer Claude Code
        rec(
            "u2",
            "a1",
            "user",
            "Day two work.",
            timestamp=at("2026-07-20", "10:05"),
            git_branch="d38-day-two",
            version="1.0.90",
        ),
        rec(
            "a2",
            "u2",
            "assistant",
            [use("Write", file_path="src/crate_wiki/claude.py")],
            timestamp=at("2026-07-20", "10:35"),
            git_branch="d38-day-two",
            version="1.0.90",
        ),
        # and again the day after
        rec(
            "u3",
            "a2",
            "user",
            "Day three work.",
            timestamp=at("2026-07-21", "11:15"),
            git_branch="d38-day-three",
            version="1.0.91",
        ),
        rec(
            "a3",
            "u3",
            "assistant",
            [{"type": "text", "text": "Shipped."}],
            timestamp=at("2026-07-21", "11:35"),
            git_branch="d38-day-three",
            version="1.0.91",
        ),
    ]


def test_a_single_day_session_yields_exactly_one_card(tmp_path):
    # The common case, and the one that must not change: a session that never crossed midnight is
    # one day and one card, the way it was before the split.
    parsed = parse_days(tmp_path, LINEAR)

    assert len(parsed) == 1
    assert parsed[0].date == "2026-07-20"  # LINEAR's UTC stamps, in the pinned UTC+10 zone
    assert parsed[0].records == len(LINEAR), "the day's own live records"


def test_a_session_resumed_twice_within_one_local_day_is_still_one_card(tmp_path):
    # Resumes are frequent and mostly same-day. The split is by *day*, not by resume, so three
    # segments of one local day are one card with all three in it.
    records = [
        rec("u1", None, "user", "Segment one.", timestamp=at("2026-07-19", "09:00")),
        rec("u2", "u1", "user", "Segment two.", timestamp=at("2026-07-19", "13:00")),
        rec("u3", "u2", "user", "Segment three.", timestamp=at("2026-07-19", "18:00")),
    ]
    parsed = parse_days(tmp_path, records)

    assert len(parsed) == 1
    rendered = parsed[0].render()
    for segment in ("Segment one.", "Segment two.", "Segment three."):
        assert segment in rendered


def test_a_session_active_on_three_days_is_three_cards_each_with_that_days_metadata(tmp_path):
    parsed = parse_days(tmp_path, day_one_two_three())

    assert [c.date for c in parsed] == ["2026-07-19", "2026-07-20", "2026-07-21"]
    assert len({c.session_id for c in parsed}) == 1, "one session, so one id across its days"

    # Each day carries the metadata in force *that* day, not the one the session started on.
    assert [c.git_branch for c in parsed] == ["d2-session-parser", "d38-day-two", "d38-day-three"]
    assert [c.tool_version for c in parsed] == ["1.0.83", "1.0.90", "1.0.91"]

    # And each day's content and span is its own — no 3-day "duration", no other day's work.
    assert [c.duration_min for c in parsed] == [30, 30, 20]
    assert parsed[0].command_count == 1 and parsed[0].files == []
    assert parsed[1].files == ["src/crate_wiki/claude.py"] and parsed[1].command_count == 0
    assert "Day two work." in parsed[1].render()
    assert "Day one work." not in parsed[1].render()

    # Three days, three filenames — `filename()` already carries the date, so nothing collides.
    assert len({c.filename() for c in parsed}) == 3
    assert parsed[2].filename().startswith("2026-07-21-")


def test_a_long_dormant_gap_cards_only_the_days_that_saw_work(tmp_path):
    # The reason `crate day` does not match on a card's *span*: a session resumed after a long
    # gap covers a stretch of calendar days that saw no work at all, and listing one card under
    # every one of them would be worse than the bug it fixes. Only active days get a card.
    records = [
        rec("u1", None, "user", "Started this.", timestamp=at("2026-07-01", "09:00")),
        rec("u2", "u1", "user", "Picked it back up.", timestamp=at("2026-07-28", "14:00")),
    ]
    parsed = parse_days(tmp_path, records)

    assert [c.date for c in parsed] == ["2026-07-01", "2026-07-28"]


def test_a_day_whose_live_records_yield_no_turns_earns_no_card(tmp_path):
    # The same test a whole file already had to pass, applied per day: a day holding only a tool
    # result handed back — noise, never intent — is not a day of work.
    records = [
        rec("u1", None, "user", "Real work.", timestamp=at("2026-07-19", "09:00")),
        rec("t1", "u1", "user", [result("12 passed")], timestamp=at("2026-07-20", "10:00")),
    ]
    parsed = parse_days(tmp_path, records)

    assert [c.date for c in parsed] == ["2026-07-19"]


def test_a_day_of_actions_with_no_user_prompt_still_earns_a_card(tmp_path):
    # Work done after a resume is work that happened that day, whether or not a fresh prompt was
    # typed. Losing it is exactly the bug — a day with commands and edits gets its card.
    records = [
        rec("u1", None, "user", "Kick it off.", timestamp=at("2026-07-19", "09:00")),
        rec(
            "a1",
            "u1",
            "assistant",
            [use("Bash", command="uv run pytest -q"), use("Edit", file_path="src/x.py")],
            timestamp=at("2026-07-20", "10:05"),
        ),
    ]
    parsed = parse_days(tmp_path, records)

    assert [c.date for c in parsed] == ["2026-07-19", "2026-07-20"]
    assert parsed[1].command_count == 1
    assert parsed[1].files == ["src/x.py"]


def test_a_day_of_only_subagent_chatter_earns_no_card(tmp_path):
    # ADR-0015 named this for Codex — a split rule must not start carding subagents. Claude's
    # sidechains are excluded from the live path before the split, so a day whose only records
    # are a subagent's internal exchange has no live conversation on it and earns nothing.
    records = [
        rec("u1", None, "user", "Delegate it.", timestamp=at("2026-07-19", "09:00")),
        rec(
            "s1",
            None,
            "user",
            "internal subagent prompt",
            isSidechain=True,
            timestamp=at("2026-07-20", "10:00"),
        ),
        rec(
            "s2",
            "s1",
            "assistant",
            [{"type": "text", "text": "internal chatter"}],
            isSidechain=True,
            timestamp=at("2026-07-20", "10:05"),
        ),
    ]
    parsed = parse_days(tmp_path, records)

    assert [c.date for c in parsed] == ["2026-07-19"]
    assert "internal chatter" not in parsed[0].render()


def test_an_unreadable_timestamp_mid_session_inherits_the_day_before_it(tmp_path):
    # `cards._day_key`'s fallback: a record whose timestamp can't be read takes the day of the
    # record before it. Records arrive in the order they happened, so the neighbour is the best
    # available answer — and it keeps one day as one card rather than splitting an undated
    # fragment off the front of it.
    records = [
        rec("u1", None, "user", "First.", timestamp=at("2026-07-19", "09:00")),
        rec("u2", "u1", "user", "Second, unstamped.", timestamp="not-a-timestamp"),
        rec("u3", "u2", "user", "Third.", timestamp=at("2026-07-19", "10:00")),
    ]
    parsed = parse_days(tmp_path, records)

    assert [c.date for c in parsed] == ["2026-07-19"]
    rendered = parsed[0].render()
    for text in ("First.", "Second, unstamped.", "Third."):
        assert text in rendered


def test_a_day_rewound_away_entirely_earns_no_card(tmp_path):
    # The Claude-specific wrinkle Codex has no analogue for (ADR-0016): the day slice is over the
    # *live path*, so a day whose records were all abandoned by a rewind has no live conversation
    # on it. Day two's records are still in the file; day three chains off day one instead.
    records = [
        rec("u1", None, "user", "Day one.", timestamp=at("2026-07-19", "09:00")),
        rec("u2", "u1", "user", "Day two, later abandoned.", timestamp=at("2026-07-20", "10:00")),
        rec("u3", "u1", "user", "Day three, from a rewind.", timestamp=at("2026-07-21", "11:00")),
    ]
    parsed = parse_days(tmp_path, records)

    assert [c.date for c in parsed] == ["2026-07-19", "2026-07-21"]
    assert "Day two, later abandoned." not in "".join(c.render() for c in parsed)


# --------------------------------------------------------------------------------------
# capture: writing into a vault, idempotently
# --------------------------------------------------------------------------------------


def make_vault(tmp_path):
    target = tmp_path / "vault"
    vault.create(target, "personal", version="0.1.0")
    return target


def capture_cards(path, target):
    """Every CaptureResult writing `path` into `target` produced, oldest day first."""
    return cards.capture(claude.parse, path, target, crate_version="0.1.0")


def capture_one(path, target):
    """The single CaptureResult a one-day session yields."""
    (result,) = capture_cards(path, target)
    return result


def card_dir(target):
    return target / "raw" / "sessions" / "claude-code"


def card_names(target):
    return sorted(path.name for path in card_dir(target).glob("*.md"))


def snapshot(paths):
    """Each card's bytes and mtime, so a re-capture can be shown not to have opened it."""
    return {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}


def test_capture_writes_a_card_into_the_vault(tmp_path, pinned_tz):
    target = make_vault(tmp_path)
    result = capture_one(write_session(tmp_path, LINEAR), target)

    assert result.written
    # LINEAR's UTC timestamps land on 2026-07-20 local under the pinned UTC+10 zone.
    card = (
        target
        / "raw"
        / "sessions"
        / "claude-code"
        / "2026-07-20-9f3a1c2e-0000-0000-0000-000000000000.md"
    )
    assert card.is_file()
    assert "Implement D2" in card.read_text()


def test_a_second_run_writes_nothing_new(tmp_path):
    target = make_vault(tmp_path)
    path = write_session(tmp_path, LINEAR)

    first = capture_one(path, target)
    card = first.card_path
    stamp = card.stat().st_mtime_ns
    marker = card.read_text() + "\nEDITED BY HAND\n"
    card.write_text(marker)  # prove the re-run doesn't touch it

    second = capture_one(path, target)
    assert not second.written
    assert card.read_text() == marker
    assert card.stat().st_mtime_ns >= stamp


def test_a_resumed_session_re_renders_the_same_card(tmp_path):
    target = make_vault(tmp_path)

    capture_one(write_session(tmp_path, LINEAR), target)

    resumed = [
        *LINEAR,
        rec("u3", "a3", "user", "One more thing.", timestamp="2026-07-19T15:20:00.000Z"),
        rec(
            "a4",
            "u3",
            "assistant",
            [use("Write", file_path="tests/test_session.py")],
            timestamp="2026-07-19T15:22:00.000Z",
        ),
    ]
    result = capture_one(write_session(tmp_path, resumed), target)

    assert result.written
    text = result.card_path.read_text()
    assert "One more thing." in text
    assert "tests/test_session.py" in text

    written = list((target / "raw" / "sessions" / "claude-code").glob("*.md"))
    assert len(written) == 1, "a resume updates the one card, it does not fragment"


def test_capture_records_the_cursor_under_its_source(tmp_path, pinned_tz):
    target = make_vault(tmp_path)
    capture_one(write_session(tmp_path, LINEAR), target)

    state = json.loads((target / ".crate" / "state.json").read_text())
    # The cursor key is session id *and* day (ADR-0015) — a Codex thread yields one card per day
    # and the id alone would collide. LINEAR's UTC stamps are 2026-07-20 under the pinned zone.
    cursor = state["claude-code"]["9f3a1c2e-0000-0000-0000-000000000000:2026-07-20"]
    assert cursor["cursor"] == "a3"


def test_capture_rewrites_a_deleted_card_even_if_the_cursor_matches(tmp_path):
    target = make_vault(tmp_path)
    path = write_session(tmp_path, LINEAR)

    first = capture_one(path, target)
    first.card_path.unlink()

    second = capture_one(path, target)
    assert second.written
    assert second.card_path.is_file()


# --------------------------------------------------------------------------------------
# re-capturing a multi-day session — the part `raw/` being immutable actually constrains
# --------------------------------------------------------------------------------------


# A session worked on across two local days, with an afternoon stretch on day one that a later
# rewind abandons. `REWIND` and `REWIND_PAST_DAY_TWO` are two different things that can be
# appended to it — both re-prompts from `a1`, on day one, so day one's own live path shortens.
DAY_ONE_TWO = [
    rec("u1", None, "user", "Start the day.", timestamp=at("2026-07-19", "09:00")),
    rec(
        "a1",
        "u1",
        "assistant",
        [{"type": "text", "text": "Started."}],
        timestamp=at("2026-07-19", "09:10"),
    ),
    rec("u2", "a1", "user", "Abandoned afterthought.", timestamp=at("2026-07-19", "16:00")),
    rec(
        "a2",
        "u2",
        "assistant",
        [{"type": "text", "text": "Doing the afterthought."}],
        timestamp=at("2026-07-19", "16:05"),
    ),
    rec("u3", "a2", "user", "Day two.", timestamp=at("2026-07-20", "09:00")),
    rec(
        "a3",
        "u3",
        "assistant",
        [{"type": "text", "text": "Day two done."}],
        timestamp=at("2026-07-20", "09:20"),
    ),
]

DAY_THREE = [
    rec("u4", "a3", "user", "Day three.", timestamp=at("2026-07-21", "10:00")),
    rec(
        "a4",
        "u4",
        "assistant",
        [{"type": "text", "text": "Day three done."}],
        timestamp=at("2026-07-21", "10:30"),
    ),
]

REWIND = [
    rec("u5", "a1", "user", "Different direction.", timestamp=at("2026-07-20", "11:00")),
    rec(
        "a5",
        "u5",
        "assistant",
        [{"type": "text", "text": "Different answer."}],
        timestamp=at("2026-07-20", "11:10"),
    ),
]

REWIND_PAST_DAY_TWO = [
    rec("u6", "a1", "user", "Picked it up much later.", timestamp=at("2026-07-21", "09:00")),
    rec(
        "a6",
        "u6",
        "assistant",
        [{"type": "text", "text": "Carrying on."}],
        timestamp=at("2026-07-21", "09:15"),
    ),
]


def append(path, records):
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(json.dumps(record) for record in records) + "\n")
    return path


def test_a_new_day_appended_adds_one_card_and_leaves_the_earlier_ones_untouched(tmp_path):
    # The whole re-capture story: `raw/` is immutable in practice and a card already cited in a
    # daily page's `sources:` must not move or change under it. Each day's cursor is that day's
    # own last live uuid, so appending Wednesday cannot disturb Monday or Tuesday.
    target = make_vault(tmp_path)
    session = write_session(tmp_path, DAY_ONE_TWO)

    first = capture_cards(session, target)
    assert len(first) == 2
    before = snapshot(result.card_path for result in first)

    append(session, DAY_THREE)
    second = capture_cards(session, target)

    written = [result for result in second if result.written]
    assert len(written) == 1, "only the new day is written"
    assert written[0].card_path.name.startswith("2026-07-21-")
    assert sum(1 for result in second if not result.written) == 2, "the earlier days are unchanged"
    for path, (content, stamp) in before.items():
        assert path.read_bytes() == content, f"{path.name} must stay byte-identical"
        assert path.stat().st_mtime_ns == stamp, f"{path.name} must not even be reopened"
    assert len(card_names(target)) == 3


def test_a_re_capture_with_nothing_appended_writes_nothing_at_all(tmp_path):
    target = make_vault(tmp_path)
    session = write_session(tmp_path, DAY_ONE_TWO)

    first = capture_cards(session, target)
    before = snapshot(result.card_path for result in first)

    second = capture_cards(session, target)

    assert [result.written for result in second] == [False, False]
    for path, (content, stamp) in before.items():
        assert path.read_bytes() == content
        assert path.stat().st_mtime_ns == stamp


def test_a_rewind_into_an_earlier_day_re_renders_that_days_card_in_place(tmp_path):
    # ADR-0016, and the one thing Codex's append-only log made impossible. A rewind re-decides the
    # live path over the *whole* file, so day one's slice shortens after day one was captured.
    # The card is rewritten where it stands: same path, no second file, nothing deleted.
    target = make_vault(tmp_path)
    session = write_session(tmp_path, DAY_ONE_TWO)

    first = capture_cards(session, target)
    day_one = first[0].card_path
    assert "Abandoned afterthought." in day_one.read_text()

    append(session, REWIND)
    second = capture_cards(session, target)

    assert [result.card_path for result in second] == [day_one, first[1].card_path], (
        "the same two paths — a rewind never renames a card out from under a `sources:` entry"
    )
    assert all(result.written for result in second)
    assert "Abandoned afterthought." not in day_one.read_text(), "day one now matches the file"
    assert "Started." in day_one.read_text(), "what survived the rewind is still there"
    assert "Different direction." in second[1].card_path.read_text()
    assert card_names(target) == [f"2026-07-19-{SID}.md", f"2026-07-20-{SID}.md"]


def test_a_rewind_past_a_whole_day_leaves_that_days_card_on_disk(tmp_path):
    # The corollary: a day with no live records left earns no card, and the one already written
    # is orphaned rather than deleted. `raw/` is immutable to Tier 1 — a daily page may cite it.
    target = make_vault(tmp_path)
    session = write_session(tmp_path, DAY_ONE_TWO)

    capture_cards(session, target)
    append(session, REWIND_PAST_DAY_TWO)
    second = capture_cards(session, target)

    assert [result.card_path.name for result in second] == [
        f"2026-07-19-{SID}.md",
        f"2026-07-21-{SID}.md",
    ], "day two has no live conversation left, so it yields nothing"
    assert card_names(target) == [
        f"2026-07-19-{SID}.md",
        f"2026-07-20-{SID}.md",
        f"2026-07-21-{SID}.md",
    ], "and its card survives on disk, stale but never deleted"


def test_crate_day_lists_a_split_session_under_every_day_it_touched(tmp_path):
    # The bug, end to end (issue #39): one card for the whole session was dated to day one, so
    # `/daily` silently lost every later day — the failure ADR-0012 exists to prevent.
    target = make_vault(tmp_path)
    capture_cards(write_session(tmp_path, day_one_two_three()), target)

    for day in ("2026-07-19", "2026-07-20", "2026-07-21"):
        assert wiki.day_cards(target, day) == [f"raw/sessions/claude-code/{day}-{SID}.md"]
    assert wiki.day_cards(target, "2026-07-22") == []
