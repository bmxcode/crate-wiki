"""Tests for the Claude Code source adapter (`claude.py`) and `cards.capture`.

Every fixture here is synthetic, built inline — no real session ever enters this repo, because
history is exposed retroactively when it goes public (see CLAUDE.md and the D2 issue).
"""

import json

from crate_wiki import cards, claude, vault

# --------------------------------------------------------------------------------------
# a tiny builder for synthetic session trees
# --------------------------------------------------------------------------------------


def rec(uuid, parent, role, content, **extra):
    """One JSONL record. `content` is a string or a list of blocks."""
    record = {
        "uuid": uuid,
        "parentUuid": parent,
        "type": role,
        "sessionId": extra.pop("session_id", "9f3a1c2e-0000-0000-0000-000000000000"),
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
    return {"uuid": uuid, "type": type_, "sessionId": "9f3a1c2e-0000-0000-0000-000000000000"}


def use(name, **params):
    return {"type": "tool_use", "name": name, "input": params}


def result(text):
    return {"type": "tool_result", "content": text}


def write_session(tmp_path, records, name="session.jsonl"):
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def parse(tmp_path, records):
    return claude.parse(write_session(tmp_path, records), crate_version="0.1.0")


# `pinned_tz` lives in conftest.py now — shared with the Codex suite.


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
    assert card.filename() == "2026-07-20-9f3a1c2e.md"


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
    assert card.filename() == "2026-07-20-9f3a1c2e.md"


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

    card = claude.parse(path, crate_version="0.1.0")
    assert card is not None
    assert "keep me" in card.render()


def test_an_empty_session_parses_to_nothing(tmp_path):
    assert parse(tmp_path, []) is None


# --------------------------------------------------------------------------------------
# capture: writing into a vault, idempotently
# --------------------------------------------------------------------------------------


def make_vault(tmp_path):
    target = tmp_path / "vault"
    vault.create(target, "personal", version="0.1.0")
    return target


def test_capture_writes_a_card_into_the_vault(tmp_path, pinned_tz):
    target = make_vault(tmp_path)
    result = cards.capture(
        claude.parse, write_session(tmp_path, LINEAR), target, crate_version="0.1.0"
    )

    assert result.written
    # LINEAR's UTC timestamps land on 2026-07-20 local under the pinned UTC+10 zone.
    card = target / "raw" / "sessions" / "claude-code" / "2026-07-20-9f3a1c2e.md"
    assert card.is_file()
    assert "Implement D2" in card.read_text()


def test_a_second_run_writes_nothing_new(tmp_path):
    target = make_vault(tmp_path)
    path = write_session(tmp_path, LINEAR)

    first = cards.capture(claude.parse, path, target, crate_version="0.1.0")
    card = first.card_path
    stamp = card.stat().st_mtime_ns
    marker = card.read_text() + "\nEDITED BY HAND\n"
    card.write_text(marker)  # prove the re-run doesn't touch it

    second = cards.capture(claude.parse, path, target, crate_version="0.1.0")
    assert not second.written
    assert card.read_text() == marker
    assert card.stat().st_mtime_ns >= stamp


def test_a_resumed_session_re_renders_the_same_card(tmp_path):
    target = make_vault(tmp_path)

    cards.capture(claude.parse, write_session(tmp_path, LINEAR), target, crate_version="0.1.0")

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
    result = cards.capture(
        claude.parse, write_session(tmp_path, resumed), target, crate_version="0.1.0"
    )

    assert result.written
    text = result.card_path.read_text()
    assert "One more thing." in text
    assert "tests/test_session.py" in text

    written = list((target / "raw" / "sessions" / "claude-code").glob("*.md"))
    assert len(written) == 1, "a resume updates the one card, it does not fragment"


def test_capture_records_the_cursor_under_its_source(tmp_path):
    target = make_vault(tmp_path)
    cards.capture(claude.parse, write_session(tmp_path, LINEAR), target, crate_version="0.1.0")

    state = json.loads((target / ".crate" / "state.json").read_text())
    cursor = state["claude-code"]["9f3a1c2e-0000-0000-0000-000000000000"]
    assert cursor["cursor"] == "a3"


def test_capture_rewrites_a_deleted_card_even_if_the_cursor_matches(tmp_path):
    target = make_vault(tmp_path)
    path = write_session(tmp_path, LINEAR)

    first = cards.capture(claude.parse, path, target, crate_version="0.1.0")
    first.card_path.unlink()

    second = cards.capture(claude.parse, path, target, crate_version="0.1.0")
    assert second.written
    assert second.card_path.is_file()
