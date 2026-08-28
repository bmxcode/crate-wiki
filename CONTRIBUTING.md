# Contributing

Thanks for looking. Here's the honest state of things.

## Pull requests

**I'm not taking pull requests right now.** This is a solo project working through a numbered set of deliverables, one per issue, and each issue is the spec for the change that closes it. A PR arriving outside that sequence is one I'd have to decline, and I'd rather say so here than waste your afternoon.

That will change once the roadmap in [Milestone 3](https://github.com/bmxcode/crate-wiki/milestone/3) is done. Until then, the useful contribution is an issue.

## Issues

Genuinely welcome, and the more specific the better:

- **A bug** — what you ran, what happened, what you expected. `crate --version`, your OS, and your Python version help. **Don't paste session transcripts, vault content, or anything from a work context** — a redacted description of the shape of the problem is enough, and this repo's history is public and permanent.
- **A command that lies** — the README and `--help` make promises, and promises rot. If one of them doesn't match what the code does, that's a real bug and I want it.
- **A design decision that looks wrong** — read the [ADR](docs/adr/) first if there is one, since it will name the alternative that was already rejected and why. If the reasoning doesn't hold, say so; that's worth more than a patch.

## Running the checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv tool install --editable . && crate --version
```

CI runs all of these plus a check that the *installed* command works, which is where a broken entrypoint hides. System Python on macOS is 3.9 and this project needs ≥3.11 — go through `uv`, never bare `python3`.

Secrets are scanned on both sides. Install the local hook once per clone:

```bash
brew install gitleaks
pre-commit install
```

## Test data

**Fixtures are synthetic, always.** The temptation is to copy a real session out of `~/.claude/projects/` to test the parser. Don't — this repo's git history is exposed retroactively, and one such commit is permanent.
