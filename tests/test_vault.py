"""Tests for `crate init` and the push policy.

Fixtures are synthetic and built in tmp_path — no real vault content reaches this repo.
"""

import shutil
import subprocess
import tomllib

import pytest
from typer.testing import CliRunner

from crate_wiki import vault
from crate_wiki.cli import app

runner = CliRunner()


def make_vault(tmp_path, scope, name="vault"):
    target = tmp_path / name
    result = runner.invoke(app, ["init", str(target), "--scope", scope])
    assert result.exit_code == 0, result.output
    return target


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)


# --------------------------------------------------------------------------------------
# the tree
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("scope", ["work", "personal"])
def test_init_creates_the_documented_tree(tmp_path, scope):
    created = make_vault(tmp_path, scope)

    for name in ("CLAUDE.md", "AGENTS.md", "index.md", "log.md", ".gitignore"):
        assert (created / name).is_file(), name

    for name in vault.WIKI_DIRS:
        assert (created / "wiki" / name).is_dir(), name

    assert (created / "raw" / "sessions" / "claude-code").is_dir()
    assert (created / "raw" / "sessions" / "codex").is_dir()
    assert (created / ".crate" / "config.toml").is_file()
    assert (created / ".crate" / "state.json").is_file()


@pytest.mark.parametrize("scope", ["work", "personal"])
def test_page_skeletons_ship_into_the_vault(tmp_path, scope):
    created = make_vault(tmp_path, scope)
    for page_type in vault.PAGE_TYPES:
        assert (created / ".crate" / "templates" / f"{page_type}.md").is_file()


def test_empty_tracked_dirs_get_a_gitkeep(tmp_path):
    created = make_vault(tmp_path, "personal")
    assert (created / "wiki" / "concepts" / ".gitkeep").is_file()
    assert (created / "raw" / "clips" / ".gitkeep").is_file()


def test_private_sections_get_no_gitkeep(tmp_path):
    # git ignores the whole directory, so a marker there could never be tracked.
    created = make_vault(tmp_path, "personal")
    assert (created / "raw" / "journal").is_dir()
    assert not (created / "raw" / "journal" / ".gitkeep").exists()


def test_init_refuses_a_non_empty_directory(tmp_path):
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "notes.md").write_text("something I care about")

    result = runner.invoke(app, ["init", str(target), "--scope", "personal"])

    assert result.exit_code == 1
    assert "refusing" in result.output
    assert (target / "notes.md").read_text() == "something I care about"


def test_init_rejects_an_unknown_scope(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path / "v"), "--scope", "secret"])
    assert result.exit_code != 0


# --------------------------------------------------------------------------------------
# scope differences
# --------------------------------------------------------------------------------------


def test_personal_has_a_journal_and_work_does_not(tmp_path):
    assert (make_vault(tmp_path, "personal", "p") / "raw" / "journal").is_dir()
    assert not (make_vault(tmp_path, "work", "w") / "raw" / "journal").exists()


def test_gitignore_covers_every_private_section_and_nothing_else(tmp_path):
    personal = (make_vault(tmp_path, "personal", "p") / ".gitignore").read_text()
    assert "raw/journal/" in personal
    for public in ("sessions", "clips", "youtube", "pastes", "assets"):
        assert f"raw/{public}/" not in personal

    work = (make_vault(tmp_path, "work", "w") / ".gitignore").read_text()
    assert "journal" not in work


def test_raw_is_committed_in_a_vault(tmp_path):
    # The engine repo gitignores raw/; a vault must not. Same line, opposite intent.
    created = make_vault(tmp_path, "personal")
    ignored = git(created, "check-ignore", "raw/sessions/claude-code/.gitkeep")
    assert ignored.returncode == 1, "raw/ must be committed in a vault"

    journal_ignored = git(created, "check-ignore", "raw/journal/anything.md")
    assert journal_ignored.returncode == 0, "a private section must be gitignored"


# --------------------------------------------------------------------------------------
# the schema — the actual deliverable
# --------------------------------------------------------------------------------------


def test_agents_md_points_at_claude_md(tmp_path):
    created = make_vault(tmp_path, "personal")
    assert "CLAUDE.md" in (created / "AGENTS.md").read_text()


def test_personal_schema_carries_the_private_section_rule(tmp_path):
    schema = (make_vault(tmp_path, "personal") / "CLAUDE.md").read_text()
    assert "`raw/journal/`" in schema
    assert "Never write anything derived from them into `wiki/`" in schema


def test_work_schema_carries_the_no_remote_rule(tmp_path):
    schema = (make_vault(tmp_path, "work") / "CLAUDE.md").read_text()
    assert "no git remote" in schema
    assert "Private sections" not in schema, "work has no private sections to describe"


def test_schema_names_the_scope(tmp_path):
    assert "Scope: **work**" in (make_vault(tmp_path, "work", "w") / "CLAUDE.md").read_text()
    assert (
        "Scope: **personal**" in (make_vault(tmp_path, "personal", "p") / "CLAUDE.md").read_text()
    )


def test_no_placeholder_survives_rendering(tmp_path):
    # safe_substitute leaves unknown $names alone, so a typo'd slot ships silently.
    for scope in ("work", "personal"):
        schema = (make_vault(tmp_path, scope, scope) / "CLAUDE.md").read_text()
        assert "$" not in schema


def test_every_skeleton_declares_a_type_the_schema_lists(tmp_path):
    # The one seam this design leaves open: the schema's table and the skeletons drifting.
    created = make_vault(tmp_path, "personal")
    schema = (created / "CLAUDE.md").read_text()

    for page_type in vault.PAGE_TYPES:
        skeleton = (created / ".crate" / "templates" / f"{page_type}.md").read_text()
        assert f"type: {page_type}\n" in skeleton
        assert f"| `{page_type}` |" in schema, f"{page_type} is not in the schema's type table"


def test_log_starts_with_an_init_entry(tmp_path):
    log = (make_vault(tmp_path, "personal") / "log.md").read_text()
    assert "init | Vault created" in log


# --------------------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------------------


def test_config_records_the_scope_and_parses(tmp_path):
    created = make_vault(tmp_path, "work")
    with (created / ".crate" / "config.toml").open("rb") as handle:
        config = tomllib.load(handle)

    assert config["scope"] == "work"
    assert config["config_version"] == vault.CONFIG_VERSION
    assert config["git"]["push_policy"] == "allowlist"
    assert config["git"]["push_allowlist"] == []


def test_config_models_sections_as_data(tmp_path):
    created = make_vault(tmp_path, "personal")
    with (created / ".crate" / "config.toml").open("rb") as handle:
        config = tomllib.load(handle)

    sections = {s["name"]: s["private"] for s in config["raw"]["sections"]}
    assert sections["journal"] is True
    assert sections["sessions"] is False


def test_load_config_rejects_a_directory_that_is_not_a_vault(tmp_path):
    with pytest.raises(vault.VaultError, match="not a crate vault"):
        vault.load_config(tmp_path)


# --------------------------------------------------------------------------------------
# push policy
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("git@github.com:me/x.git", "github.com"),
        ("https://github.com/me/x.git", "github.com"),
        ("ssh://git@git.corp.example.com:22/me/x.git", "git.corp.example.com"),
        ("https://user:token@GitHub.com/me/x.git", "github.com"),
        ("/some/local/path", None),
        ("", None),
    ],
)
def test_remote_host_reads_both_url_forms(remote, expected):
    assert vault.remote_host(remote) == expected


def test_an_empty_allowlist_refuses_everything():
    config = {"git": {"push_policy": "allowlist", "push_allowlist": []}}
    for remote in ("git@github.com:me/x.git", "https://github.com/me/x.git"):
        allowed, reason = vault.push_is_allowed(config, remote)
        assert not allowed
        assert "ADR-0001" in reason


@pytest.mark.parametrize(
    "remote",
    ["git@git.corp.example.com:me/x.git", "https://git.corp.example.com/me/x.git"],
)
def test_a_populated_allowlist_admits_a_listed_host(remote):
    config = {"git": {"push_policy": "allowlist", "push_allowlist": ["git.corp.example.com"]}}
    allowed, _ = vault.push_is_allowed(config, remote)
    assert allowed


def test_a_populated_allowlist_still_refuses_an_unlisted_host():
    config = {"git": {"push_policy": "allowlist", "push_allowlist": ["git.corp.example.com"]}}
    allowed, reason = vault.push_is_allowed(config, "git@github.com:me/personal.git")
    assert not allowed
    assert "github.com" in reason


def test_push_policy_any_allows():
    allowed, _ = vault.push_is_allowed({"git": {"push_policy": "any"}}, "git@github.com:me/x.git")
    assert allowed


def test_unknown_policy_fails_closed():
    allowed, _ = vault.push_is_allowed({"git": {"push_policy": "whatever"}}, "git@github.com:me/x")
    assert not allowed


def test_missing_git_config_fails_closed():
    # A hand-edited config that lost its [git] table must not start waving pushes through.
    allowed, _ = vault.push_is_allowed({}, "git@github.com:me/x.git")
    assert not allowed


# --------------------------------------------------------------------------------------
# git
# --------------------------------------------------------------------------------------


def test_work_vault_has_no_remote(tmp_path):
    created = make_vault(tmp_path, "work")
    assert git(created, "remote", "-v").stdout.strip() == ""


def test_work_vault_installs_an_executable_pre_push_hook(tmp_path):
    hook = make_vault(tmp_path, "work") / ".git" / "hooks" / "pre-push"
    assert hook.is_file()
    assert hook.stat().st_mode & 0o111, "the hook must be executable or git ignores it"
    assert "check-push" in hook.read_text()


def test_personal_vault_installs_no_hook(tmp_path):
    assert not (make_vault(tmp_path, "personal") / ".git" / "hooks" / "pre-push").exists()


def test_the_hook_refuses_and_explains_when_crate_is_missing(tmp_path):
    # A hook runs with git's PATH, not a login shell's, so this is a real scenario and
    # not a hypothetical. It must fail closed, and say why.
    created = make_vault(tmp_path, "work")
    hook = created / ".git" / "hooks" / "pre-push"

    result = subprocess.run(
        [str(hook), "origin", "git@github.com:me/x.git"],
        cwd=created,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )

    assert result.returncode != 0
    assert "not on PATH" in result.stderr


@pytest.mark.skipif(shutil.which("crate") is None, reason="needs the installed `crate` on PATH")
def test_work_vault_refuses_a_real_push(tmp_path):
    created = make_vault(tmp_path, "work")
    git(created, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(created, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init")

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    git(created, "remote", "add", "origin", str(remote))

    pushed = git(created, "push", "origin", "HEAD")

    assert pushed.returncode != 0, "the work vault pushed, which is the failure this prevents"
    assert "refusing to push" in pushed.stderr
