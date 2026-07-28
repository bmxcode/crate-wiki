"""Tests for the Codex source adapter (`codex.py`) and `cards.capture`.

Every fixture here is synthetic, built inline — no real Codex rollout ever enters this repo, the
same rule the Claude tests follow (see CLAUDE.md). This suite is the D7 proof: the same card
model, renderer, and cursor as `test_claude.py`, fed a different on-disk format. Where the Claude
fixtures build a `parentUuid` tree, these build a flat append-only log of `{timestamp, type,
payload}` records — Codex's real shape, minus the content.
"""

import json

import pytest

from crate_wiki import cards, claude, codex, vault

# The cross-source capture test reuses the Claude suite's synthetic session; `pinned_tz` is a
# shared fixture from conftest.py.
from test_claude import LINEAR as CLAUDE_LINEAR
from test_claude import write_session as write_claude_session

SID = "019f92a0-c0b5-7773-84ca-334c57776605"
TS = "2026-07-19T14:03:00+10:00"  # already local (UTC+10); no conversion needed for content tests


# --------------------------------------------------------------------------------------
# a tiny builder for synthetic Codex rollouts
# --------------------------------------------------------------------------------------


def env(type_, payload, ts=TS):
    """One rollout line: the `{timestamp, type, payload}` envelope every Codex record shares."""
    return {"timestamp": ts, "type": type_, "payload": payload}


def meta(ts=TS, **over):
    payload = {
        "session_id": SID,
        "id": SID,
        "cwd": "/home/me/repo/crate-wiki",
        "git": {"branch": "d7-codex-parser", "commit_hash": "abc123", "repository_url": "x"},
        "cli_version": "0.31.0",
        "originator": "codex_cli",
        "source": "cli",
    }
    payload.update(over)
    return env("session_meta", payload, ts)


def msg(role, text, ts=TS):
    ctype = "output_text" if role == "assistant" else "input_text"
    return env(
        "response_item",
        {"type": "message", "role": role, "content": [{"type": ctype, "text": text}]},
        ts,
    )


def fcall(name, args, ts=TS):
    """A `function_call` — `arguments` is a JSON-encoded string, the way Codex writes it."""
    return env(
        "response_item",
        {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": "fc1"},
        ts,
    )


def exec_cmd(cmd, ts=TS):
    return fcall("exec_command", {"cmd": cmd}, ts)


def apply_patch_body(*ops):
    """An apply-patch envelope touching the given `(op, path)` files, e.g. ("Update", "a.py")."""
    lines = ["*** Begin Patch"]
    for op, path in ops:
        lines.append(f"*** {op} File: {path}")
        if op != "Delete":
            lines += ["@@", "+a change"]
    lines.append("*** End Patch")
    return "\n".join(lines)


def patch(*ops, ts=TS):
    body = apply_patch_body(*ops)
    return env(
        "response_item",
        {"type": "custom_tool_call", "name": "apply_patch", "input": body, "call_id": "ct1"},
        ts,
    )


def reasoning(text, ts=TS):
    return env("response_item", {"type": "reasoning", "summary": [], "encrypted_content": text}, ts)


def tool_output(text, ts=TS):
    return env(
        "response_item", {"type": "function_call_output", "call_id": "fc1", "output": text}, ts
    )


def write_session(tmp_path, records, name="rollout.jsonl"):
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def parse(tmp_path, records):
    return codex.parse(write_session(tmp_path, records), crate_version="0.1.0")


# A straightforward linear Codex session used by several tests.
LINEAR = [
    meta(),
    msg("user", "Implement D7. Plan mode first."),
    msg("assistant", "On it."),
    exec_cmd("uv run pytest -q"),
    patch(("Update", "src/crate_wiki/codex.py")),
    tool_output("12 passed"),
    msg("assistant", "All green."),
]


# --------------------------------------------------------------------------------------
# what the card keeps
# --------------------------------------------------------------------------------------


def test_a_linear_codex_session_keeps_intent_prose_files_and_commands(tmp_path):
    card = parse(tmp_path, LINEAR)

    assert card is not None
    assert card.turns[0].role == "user"
    assert "Implement D7. Plan mode first." in card.turns[0].prose
    assert card.files == ["src/crate_wiki/codex.py"]
    assert card.command_count == 1

    rendered = card.render()
    assert "Implement D7. Plan mode first." in rendered
    assert "All green." in rendered
    assert "- run `uv run pytest -q`" in rendered
    assert "- edit src/crate_wiki/codex.py" in rendered


def test_each_assistant_record_folds_into_one_turn(tmp_path):
    # Codex writes each message and tool call as its own record; unfolded, every step would be a
    # turn. The card must read as user-then-assistant, the way the Claude adapter's already does.
    card = parse(tmp_path, LINEAR)
    assert [t.role for t in card.turns] == ["user", "assistant"]
    assert card.turns[1].prose.startswith("On it.")
    assert "All green." in card.turns[1].prose


def test_prose_and_actions_render_in_document_order(tmp_path):
    records = [
        meta(),
        msg("user", "Ship it."),
        msg("assistant", "First I run the tests."),
        exec_cmd("pytest"),
        msg("assistant", "Green, so I commit."),
        exec_cmd("git commit"),
    ]
    body = parse(tmp_path, records).render()
    order = [body.index(s) for s in ("First I run", "pytest", "Green, so I commit", "git commit")]
    assert order == sorted(order), "prose and actions must stay interleaved in order"


def test_apply_patch_add_update_delete_all_become_edit_actions(tmp_path):
    # One patch can touch several files at once; each *** … File: marker is one edit.
    records = [
        meta(),
        msg("user", "Refactor."),
        patch(("Add", "new.py"), ("Update", "changed.py"), ("Delete", "gone.py")),
    ]
    card = parse(tmp_path, records)
    assert card.files == ["changed.py", "gone.py", "new.py"]
    rendered = card.render()
    for path in ("new.py", "changed.py", "gone.py"):
        assert f"- edit {path}" in rendered


def test_an_argv_list_command_renders_as_one_line(tmp_path):
    records = [meta(), msg("user", "Run it."), exec_cmd(["bash", "-lc", "pytest -q"])]
    card = parse(tmp_path, records)
    assert card.command_count == 1
    assert "- run `bash -lc pytest -q`" in card.render()


def test_developer_and_injected_user_messages_never_pose_as_prompts(tmp_path):
    records = [
        meta(),
        msg("developer", "<permissions>\nfull access\n</permissions>"),
        msg("user", "<environment_context>\ncwd=/x\n</environment_context>"),
        msg("user", "Real intent here."),
        msg("assistant", "Working."),
    ]
    card = parse(tmp_path, records)

    assert [t.role for t in card.turns] == ["user", "assistant"]
    assert "Real intent here." in card.turns[0].prose
    rendered = card.render()
    assert "environment_context" not in rendered
    assert "permissions" not in rendered
    assert "full access" not in rendered


def test_replayed_agent_history_is_not_kept_as_a_prompt(tmp_path):
    # On resume and after each approval, Codex replays the prior transcript back as a user-role
    # message ("The following is the Codex agent history ..."). These are injected, often tens of
    # KB each, and must never render as prompts — they bloated the resume-segment cards otherwise.
    replay_added = "The following is the Codex agent history added since your last approval:\n" + (
        "x " * 5000
    )
    replay_req = (
        "The following is the Codex agent history whose request action you are completing:\n..."
    )
    records = [
        meta(),
        msg("user", "Please review the PR."),
        msg("assistant", "Reviewing."),
        msg("user", replay_added),
        msg("user", replay_req),
        msg("assistant", "Continuing."),
    ]
    card = parse(tmp_path, records)

    assert [t.role for t in card.turns] == ["user", "assistant"]
    assert card.turns[0].prose == "Please review the PR."
    rendered = card.render()
    assert "agent history" not in rendered
    assert len(rendered) < 2000  # the multi-KB replay block is gone, not folded into a turn


def test_reasoning_and_tool_output_bodies_are_dropped(tmp_path):
    records = [
        meta(),
        msg("user", "Go."),
        reasoning("secret internal reasoning I should not leak"),
        exec_cmd("ls"),
        tool_output("tons of stdout noise that would drown the signal"),
        msg("assistant", "Done."),
    ]
    rendered = parse(tmp_path, records).render()
    assert "secret internal reasoning" not in rendered
    assert "tons of stdout noise" not in rendered
    assert "Done." in rendered
    assert "- run `ls`" in rendered


def test_noise_function_calls_do_not_become_actions(tmp_path):
    records = [
        meta(),
        msg("user", "Plan."),
        fcall("update_plan", {"plan": [{"step": "x", "status": "pending"}]}),
        fcall("write_stdin", {"session_id": "s", "chars": "y"}),
        fcall("request_user_input", {"questions": []}),
        msg("assistant", "Planned."),
    ]
    card = parse(tmp_path, records)
    assert card.command_count == 0
    assert card.files == []
    assert card.subagent_count == 0  # Codex has no sidechains — subagents are always zero


def test_malformed_function_arguments_degrade_quietly(tmp_path):
    # A model can emit `arguments` that isn't valid JSON. It must not crash capture (ADR-0002).
    bad = env(
        "response_item",
        {"type": "function_call", "name": "exec_command", "arguments": "{not json", "call_id": "x"},
    )
    records = [meta(), msg("user", "Run."), bad, msg("assistant", "ok")]
    card = parse(tmp_path, records)
    assert card is not None
    assert card.command_count == 0  # no cmd could be recovered, but nothing raised


# --------------------------------------------------------------------------------------
# metadata, the rollup, and local time
# --------------------------------------------------------------------------------------


def test_frontmatter_carries_source_and_metadata(tmp_path):
    rendered = parse(tmp_path, LINEAR).render()
    assert "source: codex" in rendered
    assert "git_branch: d7-codex-parser" in rendered
    assert "tool_version: 0.31.0" in rendered
    assert "crate_version: 0.1.0" in rendered
    assert "files: [src/crate_wiki/codex.py]" in rendered
    assert "commands: 1" in rendered


def test_session_id_and_filename_come_from_session_meta(tmp_path, pinned_tz):
    records = [
        meta(ts="2026-07-19T14:00:00Z"),
        msg("user", "Hi.", ts="2026-07-19T14:00:00Z"),
    ]
    card = parse(tmp_path, records)
    assert card.session_id == SID
    # 14:00 UTC is 2026-07-20 00:00 local under the pinned UTC+10 zone.
    assert card.date == "2026-07-20"
    assert card.filename() == f"2026-07-20-{SID}.md"


def test_a_resumed_segment_is_keyed_by_its_own_rollout_id_not_the_thread(tmp_path):
    # Codex resumes into a NEW file whose session_meta.id is the per-file id and whose
    # session_id is the shared thread root it forked from. The card must key on `id`, or every
    # resume segment of a thread would collide onto one filename and overwrite the last.
    thread = "019f92a0-c0b5-7773-84ca-334c57776605"
    segment = "019f92a1-5032-7fc1-ae66-6fe0b8c64c54"
    records = [
        meta(session_id=thread, id=segment, parent_thread_id=thread),
        msg("user", "Continue where we left off."),
        msg("assistant", "Resuming."),
    ]
    card = parse(tmp_path, records)
    assert card.session_id == segment  # the per-file id, not the thread root
    assert card.filename() == f"{card.date}-{segment}.md"


def test_two_segments_of_one_thread_become_two_cards(tmp_path):
    # The regression the real data exposed: capturing both halves of a resumed session must not
    # collapse them onto one card (which loses whichever is captured first).
    target = make_vault(tmp_path)
    thread = "019f92a0-c0b5-7773-84ca-334c57776605"

    first = [meta(id=thread, session_id=thread), msg("user", "Part one.")]
    second = [
        meta(id="019f92a1-5032-7fc1-ae66-6fe0b8c64c54", session_id=thread, parent_thread_id=thread),
        msg("user", "Part two."),
    ]
    r1 = cards.capture(
        codex.parse, write_session(tmp_path, first, "a.jsonl"), target, crate_version="0.1.0"
    )
    r2 = cards.capture(
        codex.parse, write_session(tmp_path, second, "b.jsonl"), target, crate_version="0.1.0"
    )

    assert r1.written and r2.written
    assert r1.card_path != r2.card_path, "each rollout segment gets its own card"
    written = list((target / "raw" / "sessions" / "codex").glob("*.md"))
    assert len(written) == 2
    state = json.loads((target / ".crate" / "state.json").read_text())
    assert set(state["codex"]) == {thread, "019f92a1-5032-7fc1-ae66-6fe0b8c64c54"}


def test_a_subagent_thread_is_not_captured_as_a_session(tmp_path):
    # Codex's `guardian` auto-approver (and any multi-agent worker) runs as its own rollout file
    # marked thread_source=subagent, linked to the primary by parent_thread_id. It's Codex's
    # sidechain equivalent — pure machine chatter (approval decisions, replayed history), no user
    # intent — so it must not become a card, the way the Claude adapter drops sidechains.
    records = [
        meta(thread_source="subagent", source={"subagent": {"other": "guardian"}}),
        msg(
            "user", "The following is the Codex agent history added since your last approval:\n..."
        ),
        msg("assistant", '{"risk_level":"low","outcome":"allow","rationale":"fine"}'),
    ]
    assert parse(tmp_path, records) is None


def test_sessions_sharing_a_uuidv7_prefix_do_not_collide(tmp_path):
    # Codex ids are time-ordered UUIDv7, so two sessions started within the same ~minute share
    # their leading hex. The filename uses the full id (not a prefix) so they can't overwrite
    # each other — the collision a truncated `<date>-<short id>.md` would have caused.
    target = make_vault(tmp_path)
    a = "019f92a0-aaaa-7000-8000-000000000000"
    b = "019f92a0-bbbb-7000-8000-000000000000"  # same first 8 (019f92a0), different id
    ra = cards.capture(
        codex.parse,
        write_session(tmp_path, [meta(id=a), msg("user", "A.")], "a.jsonl"),
        target,
        crate_version="0.1.0",
    )
    rb = cards.capture(
        codex.parse,
        write_session(tmp_path, [meta(id=b), msg("user", "B.")], "b.jsonl"),
        target,
        crate_version="0.1.0",
    )
    assert ra.card_path != rb.card_path
    assert len(list((target / "raw" / "sessions" / "codex").glob("*.md"))) == 2


def test_a_late_utc_session_is_dated_and_timed_by_local_day(tmp_path, pinned_tz):
    # 23:30 UTC is 09:30 the next day under UTC+10 — Codex inherits #30's local-time dating for
    # free through the shared core, so its cards must not misfile to the UTC day either.
    records = [
        meta(ts="2026-07-19T23:30:00Z"),
        msg("user", "Late one.", ts="2026-07-19T23:30:00Z"),
    ]
    card = parse(tmp_path, records)
    assert card.date == "2026-07-20"
    rendered = card.render()
    assert "· 09:30" in rendered  # 23:30 UTC + 10h
    assert "· 23:30" not in rendered


def test_an_unparseable_timestamp_does_not_crash(tmp_path):
    records = [meta(ts="not-a-timestamp"), msg("user", "Odd.", ts="not-a-timestamp")]
    card = parse(tmp_path, records)
    assert card is not None
    assert card.turns[0].time == ""  # _hhmm's guard: blank, not a crash


def test_an_empty_or_metadata_only_session_parses_to_nothing(tmp_path):
    assert parse(tmp_path, []) is None
    assert parse(tmp_path, [meta()]) is None  # a header with no conversation is nothing usable


def test_malformed_lines_are_skipped_not_fatal(tmp_path):
    path = tmp_path / "r.jsonl"
    good = json.dumps(msg("user", "keep me"))
    path.write_text(good + "\n{ this is not json\n", encoding="utf-8")

    card = codex.parse(path, crate_version="0.1.0")
    assert card is not None
    assert "keep me" in card.render()


# --------------------------------------------------------------------------------------
# capture: writing into a vault, idempotently, alongside Claude
# --------------------------------------------------------------------------------------


def make_vault(tmp_path):
    target = tmp_path / "vault"
    vault.create(target, "personal", version="0.1.0")
    return target


def test_capture_writes_a_codex_card_under_its_own_dir(tmp_path, pinned_tz):
    target = make_vault(tmp_path)
    records = [meta(ts="2026-07-19T14:00:00Z"), msg("user", "Hi.", ts="2026-07-19T14:00:00Z")]
    result = cards.capture(
        codex.parse, write_session(tmp_path, records), target, crate_version="0.1.0"
    )

    assert result.written
    card = target / "raw" / "sessions" / "codex" / f"2026-07-20-{SID}.md"
    assert card.is_file()
    assert "Hi." in card.read_text()


def test_a_second_codex_capture_writes_nothing_new(tmp_path):
    target = make_vault(tmp_path)
    path = write_session(tmp_path, LINEAR)

    first = cards.capture(codex.parse, path, target, crate_version="0.1.0")
    stamp = first.card_path.stat().st_mtime_ns
    marker = first.card_path.read_text() + "\nEDITED BY HAND\n"
    first.card_path.write_text(marker)  # prove the re-run doesn't touch it

    second = cards.capture(codex.parse, path, target, crate_version="0.1.0")
    assert not second.written
    assert second.card_path.read_text() == marker
    assert second.card_path.stat().st_mtime_ns >= stamp


def test_a_resumed_codex_session_re_renders_the_same_card(tmp_path):
    target = make_vault(tmp_path)
    cards.capture(codex.parse, write_session(tmp_path, LINEAR), target, crate_version="0.1.0")

    resumed = [*LINEAR, msg("user", "One more thing."), exec_cmd("git push")]
    result = cards.capture(
        codex.parse, write_session(tmp_path, resumed), target, crate_version="0.1.0"
    )

    assert result.written  # more records → a new cursor → the one card is re-rendered
    text = result.card_path.read_text()
    assert "One more thing." in text
    assert "- run `git push`" in text

    written = list((target / "raw" / "sessions" / "codex").glob("*.md"))
    assert len(written) == 1, "a resume updates the one card, it does not fragment"


def test_the_codex_cursor_is_the_record_count_under_its_own_source(tmp_path):
    target = make_vault(tmp_path)
    cards.capture(codex.parse, write_session(tmp_path, LINEAR), target, crate_version="0.1.0")

    state = json.loads((target / ".crate" / "state.json").read_text())
    cursor = state["codex"][SID]
    assert cursor["cursor"] == str(len(LINEAR))
    assert cursor["records"] == len(LINEAR)


def test_capturing_codex_does_not_disturb_the_claude_cursor(tmp_path):
    # Both sources into one vault: the state file keeps a block per source, and the cards land in
    # sibling directories. This is the whole point of D7 — a second front-end, no collision.
    target = make_vault(tmp_path)
    cards.capture(
        claude.parse, write_claude_session(tmp_path, CLAUDE_LINEAR), target, crate_version="0.1.0"
    )
    cards.capture(codex.parse, write_session(tmp_path, LINEAR), target, crate_version="0.1.0")

    state = json.loads((target / ".crate" / "state.json").read_text())
    assert set(state) == {"claude-code", "codex"}
    assert state["claude-code"]["9f3a1c2e-0000-0000-0000-000000000000"]["cursor"] == "a3"
    assert state["codex"][SID]["cursor"] == str(len(LINEAR))

    assert list((target / "raw" / "sessions" / "claude-code").glob("*.md"))
    assert list((target / "raw" / "sessions" / "codex").glob("*.md"))


# --------------------------------------------------------------------------------------
# capture_all — the manual sweep. Codex has no Stop hook, so this is the front door that
# replaces one: point it at a sessions dir and it captures every new/changed rollout in it.
# --------------------------------------------------------------------------------------


def write_rollout(sessions_dir, name, records):
    """A rollout file named the way `discover`'s glob expects, inside a sessions dir."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / name
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def test_capture_all_scans_captures_and_skips_across_a_sessions_dir(tmp_path):
    target = make_vault(tmp_path)
    sessions = tmp_path / "sessions"
    write_rollout(sessions, "rollout-a.jsonl", [meta(id="a-session"), msg("user", "Session A.")])
    write_rollout(sessions, "rollout-b.jsonl", [meta(id="b-session"), msg("user", "Session B.")])
    write_rollout(
        sessions, "rollout-guardian.jsonl", [meta(id="g-session", thread_source="subagent")]
    )
    write_rollout(sessions, "rollout-empty.jsonl", [meta(id="empty-session")])

    summary = codex.capture_all(target, crate_version="0.1.0", sessions_dir=sessions)

    assert summary.scanned == 4
    assert len(summary.captured) == 2
    assert summary.unchanged == 0
    assert summary.skipped == 2  # the guardian thread and the metadata-only file
    written = list((target / "raw" / "sessions" / "codex").glob("*.md"))
    assert len(written) == 2


def test_a_rollout_that_cannot_be_read_is_skipped_not_fatal(tmp_path):
    # A directory whose name matches the glob: discover() finds it like any other rollout, but
    # reading it as text raises — the per-file guard has to catch that, not just a None parse.
    target = make_vault(tmp_path)
    sessions = tmp_path / "sessions"
    write_rollout(sessions, "rollout-a.jsonl", [meta(id="a-session"), msg("user", "Good.")])
    (sessions / "rollout-bad.jsonl").mkdir(parents=True)

    summary = codex.capture_all(target, crate_version="0.1.0", sessions_dir=sessions)

    assert summary.scanned == 2
    assert len(summary.captured) == 1
    assert summary.skipped == 1


def test_capture_all_is_idempotent(tmp_path):
    target = make_vault(tmp_path)
    sessions = tmp_path / "sessions"
    write_rollout(sessions, "rollout-a.jsonl", LINEAR)

    first = codex.capture_all(target, crate_version="0.1.0", sessions_dir=sessions)
    card_path = first.captured[0]
    stamp = card_path.stat().st_mtime_ns
    marker = card_path.read_text() + "\nEDITED BY HAND\n"
    card_path.write_text(marker)  # prove the re-run doesn't touch it

    second = codex.capture_all(target, crate_version="0.1.0", sessions_dir=sessions)

    assert second.captured == []
    assert second.unchanged == 1
    assert second.skipped == 0
    assert card_path.read_text() == marker
    assert card_path.stat().st_mtime_ns >= stamp


def test_capture_all_gives_a_resumed_thread_two_cards_in_one_sweep(tmp_path):
    # The same regression test_two_segments_of_one_thread_become_two_cards covers for a single
    # `cards.capture` call, exercised through a sweep of both segments at once.
    target = make_vault(tmp_path)
    sessions = tmp_path / "sessions"
    thread = "019f92a0-c0b5-7773-84ca-334c57776605"
    segment = "019f92a1-5032-7fc1-ae66-6fe0b8c64c54"
    write_rollout(
        sessions, "rollout-1.jsonl", [meta(id=thread, session_id=thread), msg("user", "Part one.")]
    )
    write_rollout(
        sessions,
        "rollout-2.jsonl",
        [meta(id=segment, session_id=thread, parent_thread_id=thread), msg("user", "Part two.")],
    )

    summary = codex.capture_all(target, crate_version="0.1.0", sessions_dir=sessions)

    assert summary.scanned == 2
    assert len(summary.captured) == 2
    written = list((target / "raw" / "sessions" / "codex").glob("*.md"))
    assert len(written) == 2


def test_capture_all_on_a_missing_sessions_dir_is_an_empty_summary(tmp_path):
    target = make_vault(tmp_path)
    summary = codex.capture_all(
        target, crate_version="0.1.0", sessions_dir=tmp_path / "does-not-exist"
    )
    assert summary == codex.ScanSummary(0, [], 0, 0)


def test_capture_all_still_validates_the_vault_even_with_nothing_to_scan(tmp_path):
    with pytest.raises(vault.VaultError):
        codex.capture_all(
            tmp_path / "not-a-vault",
            crate_version="0.1.0",
            sessions_dir=tmp_path / "does-not-exist",
        )


def test_discover_finds_rollouts_oldest_first(tmp_path):
    sessions = tmp_path / "sessions"
    write_rollout(sessions, "rollout-2026-07-19T14-00-00-b.jsonl", [meta()])
    write_rollout(sessions, "rollout-2026-07-19T09-00-00-a.jsonl", [meta()])

    found = codex.discover(sessions)

    assert [p.name for p in found] == [
        "rollout-2026-07-19T09-00-00-a.jsonl",
        "rollout-2026-07-19T14-00-00-b.jsonl",
    ]


def test_discover_on_a_missing_dir_is_empty(tmp_path):
    assert codex.discover(tmp_path / "nope") == []


# --------------------------------------------------------------------------------------
# count_unswept — issue #35: has `capture_all` seen everything `discover()` finds, without
# re-parsing a single rollout to check.
# --------------------------------------------------------------------------------------


def test_count_unswept_with_no_prior_sweep_counts_everything(tmp_path):
    target = make_vault(tmp_path)
    sessions = tmp_path / "sessions"
    write_rollout(sessions, "rollout-a.jsonl", [meta(id="a-session")])
    write_rollout(sessions, "rollout-b.jsonl", [meta(id="b-session")])

    assert codex.count_unswept(target, sessions_dir=sessions) == 2


def test_count_unswept_is_zero_right_after_a_sweep(tmp_path):
    target = make_vault(tmp_path)
    sessions = tmp_path / "sessions"
    write_rollout(sessions, "rollout-a.jsonl", [meta(id="a-session"), msg("user", "Hi.")])
    write_rollout(sessions, "rollout-b.jsonl", [meta(id="b-session"), msg("user", "Hi.")])

    codex.capture_all(target, crate_version="0.1.0", sessions_dir=sessions)

    assert codex.count_unswept(target, sessions_dir=sessions) == 0


def test_count_unswept_counts_a_rollout_added_after_the_last_sweep(tmp_path):
    target = make_vault(tmp_path)
    sessions = tmp_path / "sessions"
    write_rollout(sessions, "rollout-a.jsonl", [meta(id="a-session"), msg("user", "Hi.")])
    codex.capture_all(target, crate_version="0.1.0", sessions_dir=sessions)

    write_rollout(sessions, "rollout-c.jsonl", [meta(id="c-session"), msg("user", "New.")])

    assert codex.count_unswept(target, sessions_dir=sessions) == 1


def test_count_unswept_treats_a_skipped_rollout_as_swept(tmp_path):
    # A subagent thread is skipped by capture_all (never gets a card, never gets a `"codex"`
    # cursor entry) but the sweep still looked at it — it must not show up as unswept forever.
    target = make_vault(tmp_path)
    sessions = tmp_path / "sessions"
    write_rollout(
        sessions, "rollout-guardian.jsonl", [meta(id="g-session", thread_source="subagent")]
    )

    codex.capture_all(target, crate_version="0.1.0", sessions_dir=sessions)

    assert codex.count_unswept(target, sessions_dir=sessions) == 0


def test_count_unswept_on_a_missing_sessions_dir_is_zero(tmp_path):
    target = make_vault(tmp_path)
    assert codex.count_unswept(target, sessions_dir=tmp_path / "does-not-exist") == 0
