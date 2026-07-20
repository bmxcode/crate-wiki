"""Tests for the Stop-hook front-end and the settings.json installer.

Synthetic fixtures only — the session trees come from test_session.py's inline builders, and no
real session, settings file, or home directory is ever touched (the log is redirected into a
tmp dir for every test). The contract under test is ADR-0002's: capture must never break session
exit, so `crate capture claude` always exits 0 and logs, whatever it's handed.
"""

import json

import pytest
from typer.testing import CliRunner

from crate_wiki import hook, vault
from crate_wiki.cli import app
from test_session import LINEAR, write_session

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_log(monkeypatch, tmp_path):
    """Send every hook log line into the test's tmp dir, never the real ~/.claude."""
    monkeypatch.setattr(hook, "LOG_PATH", tmp_path / "crate-capture.log")


def make_vault(tmp_path, name="vault"):
    target = tmp_path / name
    vault.create(target, "personal", version="0.1.0")
    return target


def card_dir(vault_path):
    return vault_path / "raw" / "sessions" / "claude-code"


def log_text(tmp_path):
    return (tmp_path / "crate-capture.log").read_text()


# --------------------------------------------------------------------------------------
# the fail-quiet contract: exit 0, write nothing, never raise
# --------------------------------------------------------------------------------------


def test_malformed_stdin_exits_zero_and_writes_nothing(tmp_path):
    target = make_vault(tmp_path)
    result = runner.invoke(app, ["capture", "claude", "--vault", str(target)], input="not json{")
    assert result.exit_code == 0
    assert list(card_dir(target).glob("*.md")) == []


def test_missing_transcript_exits_zero_and_writes_nothing(tmp_path):
    target = make_vault(tmp_path)
    payload = json.dumps({"transcript_path": str(tmp_path / "ghost.jsonl")})
    result = runner.invoke(app, ["capture", "claude", "--vault", str(target)], input=payload)
    assert result.exit_code == 0
    assert list(card_dir(target).glob("*.md")) == []


def test_broken_vault_exits_zero_and_logs_the_error(tmp_path):
    path = write_session(tmp_path, LINEAR)
    payload = json.dumps({"transcript_path": str(path)})
    result = runner.invoke(
        app, ["capture", "claude", "--vault", str(tmp_path / "nope")], input=payload
    )
    assert result.exit_code == 0
    assert not (tmp_path / "nope").exists()  # a phantom vault yields no writes at all
    assert "error" in log_text(tmp_path)


def test_no_vault_exits_zero(tmp_path):
    path = write_session(tmp_path, LINEAR)
    payload = json.dumps({"transcript_path": str(path)})
    result = runner.invoke(app, ["capture", "claude"], input=payload)
    assert result.exit_code == 0
    assert "no --vault" in log_text(tmp_path)


# --------------------------------------------------------------------------------------
# the happy path, both stdin and --transcript
# --------------------------------------------------------------------------------------


def test_stdin_payload_captures_a_card(tmp_path):
    target = make_vault(tmp_path)
    path = write_session(tmp_path, LINEAR)
    payload = json.dumps({"transcript_path": str(path), "hook_event_name": "Stop"})

    result = runner.invoke(app, ["capture", "claude", "--vault", str(target)], input=payload)

    assert result.exit_code == 0
    cards = list(card_dir(target).glob("*.md"))
    assert len(cards) == 1
    assert "Implement D2" in cards[0].read_text()
    assert "captured" in log_text(tmp_path)


def test_transcript_flag_captures_without_stdin(tmp_path):
    target = make_vault(tmp_path)
    path = write_session(tmp_path, LINEAR)

    result = runner.invoke(
        app, ["capture", "claude", "--vault", str(target), "--transcript", str(path)]
    )

    assert result.exit_code == 0
    assert len(list(card_dir(target).glob("*.md"))) == 1


def test_a_second_capture_logs_already_and_does_not_duplicate(tmp_path):
    target = make_vault(tmp_path)
    path = write_session(tmp_path, LINEAR)
    args = ["capture", "claude", "--vault", str(target), "--transcript", str(path)]

    runner.invoke(app, args)
    runner.invoke(app, args)

    assert len(list(card_dir(target).glob("*.md"))) == 1
    assert "already captured" in log_text(tmp_path)


# --------------------------------------------------------------------------------------
# install(): merge into settings.json without clobbering
# --------------------------------------------------------------------------------------


def test_install_creates_the_stop_hook(tmp_path):
    target = make_vault(tmp_path)
    settings = tmp_path / "settings.json"

    status = hook.install(target, settings_path=settings, crate_bin="crate")

    assert "installed" in status
    command = json.loads(settings.read_text())["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "capture claude" in command
    assert str(target.resolve()) in command  # the vault is baked into the command


def test_install_preserves_existing_stop_hooks_and_other_keys(tmp_path):
    target = make_vault(tmp_path)
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
                "model": "opus",
            }
        )
    )

    hook.install(target, settings_path=settings, crate_bin="crate")

    data = json.loads(settings.read_text())
    commands = [entry["command"] for group in data["hooks"]["Stop"] for entry in group["hooks"]]
    assert "echo hi" in commands
    assert any("capture claude" in command for command in commands)
    assert data["model"] == "opus"  # an unrelated key is left untouched


def test_install_is_idempotent(tmp_path):
    target = make_vault(tmp_path)
    settings = tmp_path / "settings.json"
    hook.install(target, settings_path=settings, crate_bin="crate")

    status = hook.install(target, settings_path=settings, crate_bin="crate")

    assert status == "already installed"
    data = json.loads(settings.read_text())
    ours = _our_commands(data)
    assert len(ours) == 1  # no duplicate entry


def test_install_updates_a_different_vault_in_place(tmp_path):
    v1 = make_vault(tmp_path, "v1")
    v2 = make_vault(tmp_path, "v2")
    settings = tmp_path / "settings.json"
    hook.install(v1, settings_path=settings, crate_bin="crate")

    status = hook.install(v2, settings_path=settings, crate_bin="crate")

    assert "updated" in status
    ours = _our_commands(json.loads(settings.read_text()))
    assert len(ours) == 1
    assert str(v2.resolve()) in ours[0]


def test_install_refuses_a_malformed_settings_file(tmp_path):
    target = make_vault(tmp_path)
    settings = tmp_path / "settings.json"
    settings.write_text("{ not json")

    with pytest.raises(vault.VaultError):
        hook.install(target, settings_path=settings, crate_bin="crate")


def test_install_refuses_a_non_vault(tmp_path):
    settings = tmp_path / "settings.json"
    with pytest.raises(vault.VaultError):
        hook.install(tmp_path / "nope", settings_path=settings, crate_bin="crate")


def _our_commands(settings: dict) -> list[str]:
    return [
        entry["command"]
        for group in settings["hooks"]["Stop"]
        for entry in group["hooks"]
        if "capture claude" in entry["command"]
    ]
