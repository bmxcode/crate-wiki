"""Test-wide setup. Currently one thing: an optional clock, so date bugs surface on the PR.

D4 shipped tests that passed on the day they were written and failed four days later. The fixture
wrote a raw file with mtime *now* and dated its page in the past, so staleness detection correctly
flagged it the moment the real calendar moved on — green when it merged, red on `main` by the time
anyone looked. Nothing in CI could have caught it, because the trigger was the date rather than a
commit.

Setting `CRATE_TEST_CLOCK` moves `date.today()` far into the future for the whole suite:

    CRATE_TEST_CLOCK=2027-03-01 uv run pytest -q

This reaches the engine, not just the tests: pytest imports conftest before the test modules that
import `crate_wiki`, so its `from datetime import date` binds to the patched class. Without the
variable set, nothing here does anything.

**What it does and doesn't catch.** It catches a test that asserts on the real current date — the
easiest version of this mistake to make, and invisible until the year turns over. It does *not*
catch the D4 bug itself: that one compared a file's mtime against a hardcoded date, and `os.utime`
is real-clock regardless of what `date.today()` says. Mtime drift is caught by the nightly CI run,
and prevented outright by fixtures pinning mtime rather than letting it default to now.
"""

import datetime
import os
import time

import pytest

_CLOCK = os.environ.get("CRATE_TEST_CLOCK")

if _CLOCK:

    class _FixedDate(datetime.date):
        """`date`, with today pinned. Everything else — arithmetic, parsing — is unchanged."""

        @classmethod
        def today(cls) -> "_FixedDate":
            return cls.fromisoformat(_CLOCK)

    datetime.date = _FixedDate


@pytest.fixture
def pinned_tz():
    """Pin the process timezone to a fixed UTC+10, no-DST zone for one test.

    Session dates/times are local, so a test asserting on them would pass under the test runner's
    timezone (commonly UTC in CI) and fail on a contributor's machine that isn't UTC — the same
    class of environment-dependent flake `CRATE_TEST_CLOCK` above exists for. Pin it explicitly
    instead of inheriting the runner's. Shared here so both the Claude and Codex suites use it.

    `time.tzset()` mutates process-wide state, so both the set and the restore have to go through
    it, here — `monkeypatch.setenv` reverts `os.environ` on teardown but never calls `tzset()`
    itself, which would leave every later test's `astimezone()` reading the pinned zone.
    """
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "Etc/GMT-10"  # UTC+10 — POSIX inverts the Etc/GMT sign
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()
