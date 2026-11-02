"""Snapshot test: assembled pytest command + env for the existing single-
worker run path must be byte-identical with and without --workers 1.

We intercept ``subprocess.run`` to capture (cmd, env) without actually
executing pytest. That captured tuple is what conftest.py.j2 reads to
configure the test run, so byte-identical inputs guarantee byte-identical
behavior.

Why this matters:
- v0.1 sacred invariant: ``steppy run`` (no flags) and ``steppy run
  --workers 1`` produce identical behavior to today's main branch. Changed
  once on purpose, in 0.10: every run is now recorded (``--junitxml`` at the
  run's fixed path, ``STEPPY_EVENTS_DIR`` / ``STEPPY_RUN_ID`` /
  ``STEPPY_STEP_EVENTS``), which ``--bundle`` used to opt into.
- This test catches accidental drift in how cli.run constructs pytest_cmd
  or env (env-var ordering, flag changes, etc.) between the two
  invocations.

The reference snapshot (what main produces) lives inline below — derived
from cli.py:572-706. If the existing path is intentionally changed, update
the snapshot and the change is visible in code review.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner


@pytest.fixture
def fake_tests_dir(tmp_path: Path, monkeypatch) -> Path:
    """Minimal tests/e2e tree that satisfies cli.run's pre-flight checks.

    The working directory moves there too: every run writes its records under
    ``./output/``, which must not land in the checkout."""
    monkeypatch.chdir(tmp_path)
    tests_dir = tmp_path / "tests" / "e2e"
    tests_dir.mkdir(parents=True)
    (tests_dir / "conftest.py").write_text("# conftest", encoding="utf-8")
    (tests_dir / "test_example.py").write_text("# test", encoding="utf-8")
    return tests_dir


def _capture_subprocess_call(fake_tests_dir: Path, extra_args: list[str]) -> tuple[list[str], dict]:
    """Invoke `steppy run <args>` with subprocess.run patched to capture
    arguments. Returns the (cmd, env) that would have been passed to pytest."""
    captured = {}

    def fake_run(cmd, env=None, cwd=None, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env or os.environ)
        result = MagicMock()
        result.returncode = 0
        return result

    from steppy.cli import app

    runner = CliRunner()
    # A fixed run id: the id is part of the argv (the --junitxml path) and the
    # env, and two invocations must still compare equal.
    with (
        patch("steppy.cli.subprocess.run", side_effect=fake_run),
        patch("steppy.cli.new_run_id", return_value="20260101T000000Z-abcdef"),
    ):
        runner.invoke(app, ["run", "--tests-dir", str(fake_tests_dir), *extra_args])

    return captured.get("cmd", []), captured.get("env", {})


# Env-var keys we expect to differ run-to-run (process PIDs, terminal sessions,
# etc.). We strip them before comparing snapshots.
_VOLATILE_ENV_KEYS = {"_", "PWD", "OLDPWD", "SHLVL", "PYTHONHASHSEED"}


def _normalize_env(env: dict) -> dict:
    return {k: v for k, v in env.items() if k not in _VOLATILE_ENV_KEYS}


def _steppy_env_keys(env: dict) -> dict:
    """The subset of env that's actually about steppy/playwright behavior —
    everything else is inherited from os.environ and not load-bearing for
    byte-identical behavior. Comparing this subset gives a stable snapshot."""
    return {k: v for k, v in env.items() if k.startswith(("STEPPY_", "HEADED"))}


def test_no_workers_flag_matches_workers_1(fake_tests_dir):
    """Default invocation == --workers 1 (both should produce identical
    pytest cmd + steppy env subset)."""
    cmd_default, env_default = _capture_subprocess_call(fake_tests_dir, [])
    cmd_w1, env_w1 = _capture_subprocess_call(fake_tests_dir, ["--workers", "1"])
    assert cmd_default == cmd_w1, (
        f"pytest cmd diverges between default and --workers 1:\n  default: {cmd_default}\n  w=1:     {cmd_w1}"
    )
    assert _steppy_env_keys(env_default) == _steppy_env_keys(env_w1), (
        f"STEPPY_ env diverges:\n  default: {_steppy_env_keys(env_default)}\n  w=1:     {_steppy_env_keys(env_w1)}"
    )


def test_workers_1_produces_expected_pytest_cmd(fake_tests_dir):
    """Reference snapshot of the pytest command for `steppy run --workers 1`
    against a fake tests dir. If this fails, either:
      (a) the existing path was intentionally changed — update the snapshot
      (b) the parallel dispatch leaked into the single-worker path — fix it
    """
    cmd, _env = _capture_subprocess_call(fake_tests_dir, ["--workers", "1"])
    assert cmd[0] == sys.executable
    assert cmd[1:3] == ["-m", "pytest"]
    # Last positional arg is the tests dir (no scenario/tag filter).
    assert cmd[-4] == str(fake_tests_dir)
    assert cmd[-3:-1] == ["--browser", "chromium"]
    # Every run is recorded (0.10): the run's JUnit file, at its fixed path.
    expected = Path("output") / "junit" / "20260101T000000Z-abcdef" / "run.xml"
    assert cmd[-1] == f"--junitxml={Path.cwd() / expected}"


def test_workers_1_sets_only_the_recording_env_vars(fake_tests_dir):
    """With no flags, the existing path sets exactly the env vars that record
    the run (0.10; before, none) — plus HEADED, and only when --headed."""
    _cmd, env = _capture_subprocess_call(fake_tests_dir, ["--workers", "1"])
    steppy_keys = _steppy_env_keys(env)
    assert steppy_keys == {
        "STEPPY_RUN_ID": "20260101T000000Z-abcdef",
        "STEPPY_EVENTS_DIR": str(Path.cwd() / "output" / "events" / "20260101T000000Z-abcdef"),
        "STEPPY_STEP_EVENTS": "1",
    }, f"unexpected STEPPY_ env vars set on default run: {steppy_keys}"


def test_workers_1_with_screenshots_sets_env(fake_tests_dir):
    """The --screenshots flag sets STEPPY_SCREENSHOTS=1 — sanity check that
    the existing flag-handling logic still fires for workers=1."""
    _cmd, env = _capture_subprocess_call(fake_tests_dir, ["--workers", "1", "--screenshots"])
    assert env.get("STEPPY_SCREENSHOTS") == "1"


def test_workers_1_no_parallel_event_machinery(fake_tests_dir):
    """The parallel mode's structured-events plumbing (STEPPY_EMIT_EVENTS=1
    and ``-q --tb=short`` on the pytest argv) must NOT leak into the
    single-worker path. Guards the byte-identical invariant against the
    worker-side gate we add for parallel visibility."""
    cmd, env = _capture_subprocess_call(fake_tests_dir, ["--workers", "1"])
    assert "STEPPY_EMIT_EVENTS" not in env, (
        f"STEPPY_EMIT_EVENTS leaked into single-worker env: {env.get('STEPPY_EMIT_EVENTS')!r}"
    )
    assert "-q" not in cmd, f"pytest -q leaked into single-worker argv: {cmd}"
    assert "--tb=short" not in cmd, f"--tb=short leaked into single-worker argv: {cmd}"
    assert "no:cacheprovider" not in cmd, f"pytest cacheprovider disable leaked into single-worker argv: {cmd}"
