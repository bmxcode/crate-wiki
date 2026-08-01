from typer.testing import CliRunner

from crate_wiki import __version__
from crate_wiki.cli import app

# The Codex suite owns the synthetic-rollout builders; the sweep's *output* is a CLI concern, so
# the assertion about it lives here. (test_codex.py imports from test_claude.py the same way.)
from test_codex import day_one_two_three, make_vault, write_rollout

runner = CliRunner()


def test_version_flag_reports_the_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_names_the_tool():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "crate" in result.output


def test_the_codex_sweep_summary_names_rollouts_and_cards_separately(tmp_path, pinned_tz):
    # A thread active on three days is one rollout and three cards (ADR-0015), so the two halves
    # of the summary count different things. Bare numbers would read as a contradiction.
    target = make_vault(tmp_path)
    sessions = tmp_path / "sessions"
    write_rollout(sessions, "rollout-1.jsonl", day_one_two_three())

    result = runner.invoke(
        app,
        ["capture", "codex", "--vault", str(target), "--sessions-dir", str(sessions)],
    )

    assert result.exit_code == 0
    assert "scanned 1 rollout, skipped 0; captured 3 cards, unchanged 0" in result.output
