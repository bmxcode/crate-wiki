# Going public

This repo is built to be public — the engine holds no vault content, and `raw/` is gitignored ([ADR-0003](adr/0003-engine-vaults-over-fork.md)). But **git history is exposed retroactively**: the day the repo flips, every past commit becomes readable at once. So the flip is gated on history being clean, not on the code being finished. This is the runbook for it; [issue tracking the flip] holds the live checklist.

## Before the flip

- **Secret scanning is wired both sides.** A [pre-commit hook](../.pre-commit-config.yaml) blocks secrets locally, and a `secrets` job in [CI](../.github/workflows/ci.yml) scans full history on every push and PR. Both run the same pinned gitleaks. Contributors need `brew install gitleaks` + `pre-commit install`; a contributor who skips that is still caught by CI.
- **History has been scanned clean.** Re-run before flipping if there've been new commits:

  ```bash
  gitleaks git --redact --verbose
  ```

- **README and repo description** describe the current state, not a half-built one.

## At the flip

Branch protection on GitHub Free is enforced **only on public repos**, so these run *after* the repo is public — they 403 while private. Push `main` and let CI run once first, so the `check` and `secrets` status checks are known to GitHub before they're required.

```bash
REPO=bmxcode/crate-wiki

# Protect main: require a PR + green CI for non-admins, enforce the linear history the squash-merge
# flow already keeps, block force-push / deletion. enforce_admins=false keeps direct push available
# to you in a pinch; outside contributors go fork -> PR. required_approving_review_count=0 means a
# PR is required but you can self-merge — the right setting solo, since 1 would lock you out.
gh api --method PUT "repos/$REPO/branches/main/protection" --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "checks": [
      { "context": "check" },
      { "context": "secrets" }
    ]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0,
    "dismiss_stale_reviews": true
  },
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true,
  "restrictions": null
}
JSON

# Platform-side secret scanning + push protection — blocks a secret at push time, before the remote.
gh api --method PATCH "repos/$REPO" \
  -f 'security_and_analysis[secret_scanning][status]=enabled' \
  -f 'security_and_analysis[secret_scanning_push_protection][status]=enabled'
```

## Branching, once public

Trunk-based, unchanged from now: `main` is the trunk, short-lived `dN-name` deliverable branches, squash-merge and delete, tags (not release branches) for releases. What changes is only the gate — branch protection and CI stand in for the self-review you do now, and outside contributors arrive via fork → PR.
