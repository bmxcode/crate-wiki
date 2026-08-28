<!-- One deliverable per branch, one issue per deliverable. The issue is the spec. -->

Closes #

## What this changes



## Where the boundary falls

<!-- Anything with a single right answer is code; judgment is a prompt (ADR-0004, ADR-0008). Tick what this touches. -->

- [ ] CLI — a deterministic primitive
- [ ] A parser or the card core
- [ ] A slash command / the vault schema
- [ ] `docs/` only

## Checklist

- [ ] No real session data, vault content, client names or internal paths — fixtures are synthetic
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pytest -q` passes
- [ ] `uv tool install --editable . && crate --version` works
- [ ] An ADR if a real alternative was rejected — and none if one wasn't
