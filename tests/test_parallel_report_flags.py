"""``--trace`` / ``--video`` / ``--retries`` / ``--`` passthrough must reach the
workers in parallel mode.

Before this, all four were silently dropped for ``steppy run --workers N``:
``cli.run``'s parallel dispatch never forwarded them, ``render_override``
emitted no ``STEPPY_TRACE``/``STEPPY_VIDEO``, ``worker._build_pytest_argv``
hardcoded the argv, and ``pytest_passthrough`` was an unread parameter on
``coordinator.run_parallel``. So in exactly the mode where debugging is
hardest, the most useful artifact (a Playwright trace) was unavailable.

Two halves, both Docker-free:
  * override rendering — the env block a worker container receives;
  * worker argv — what ``steppy worker`` actually hands to pytest.

The byte-identical assertions are the load-bearing ones: with none of the
features in play the rendered override must be **unchanged**, so existing
projects see no diff (mirrors tests/test_parallel_preauth.py).
"""

from __future__ import annotations

from pathlib import Path

from steppy._parallel.compose import ComposeIntrospection, render_override
from steppy.worker import _build_pytest_argv


def _render(**kwargs) -> str:
    """Render an override with the fixed baseline args, plus overrides."""
    base = dict(
        introspection=ComposeIntrospection(services_with_ports=["frontend"], networks=["appnet"]),
        worker_image="steppy-worker",
        image_tag="0.3.0-abc",
        worker_index=1,
        run_id="deadbeef",
        project_root=Path("/proj"),
        tmp_dir=Path("/tmp/run"),
        base_url_override='{"editor": "http://app:80"}',
        locales="fr,nl",
        layouts="desktop",
        guide_mode="",
        no_asserts="",
        screenshots="",
    )
    base.update(kwargs)
    return render_override(**base)


# ── Inertness: unset features change nothing ────────────────────────────────


def test_override_byte_identical_when_no_report_flags():
    """The whole point of conditional emission: a project that uses none of
    these features must get exactly the YAML it got before the feature."""
    assert _render() == _render(trace_mode="off", video_mode="off", retries=0, pytest_extra=[])


def test_no_report_env_lines_when_unset():
    yml = _render()
    for key in ("STEPPY_TRACE", "STEPPY_VIDEO", "STEPPY_RETRIES", "STEPPY_PYTEST_EXTRA"):
        assert key not in yml, f"{key} leaked into an override that did not ask for it"


def test_default_coord_dir_and_auth_mount_unchanged():
    """``coord_subdir`` / ``auth_mount`` (used by the retry pass and
    isolate_auth) must be inert by default."""
    yml = _render()
    assert "STEPPY_COORD_DIR: /steppy/coord\n" in yml
    # Bind sources are absolutised by render_override (drive letter on Windows),
    # so match the suffix rather than the whole host path.
    assert "/proj/.auth:/steppy/project/.auth" in yml


# ── The env block actually carries the settings ─────────────────────────────


def test_trace_and_video_reach_the_worker_env():
    yml = _render(trace_mode="on-failure", video_mode="on")
    assert "STEPPY_TRACE: 'on-failure'" in yml
    assert "STEPPY_VIDEO: 'on'" in yml


def test_retries_reach_the_worker_env():
    assert "STEPPY_RETRIES: '3'" in _render(retries=3)


def test_pytest_extra_is_shlex_encoded():
    yml = _render(pytest_extra=["-k", "login and not slow", "--maxfail=2"])
    assert "STEPPY_PYTEST_EXTRA: '-k 'login and not slow' --maxfail=2'" not in yml, (
        "single quotes must be YAML-escaped by doubling"
    )
    assert "STEPPY_PYTEST_EXTRA:" in yml
    # Round-trips through the YAML single-quote escape (doubled quotes).
    line = next(ln for ln in yml.splitlines() if "STEPPY_PYTEST_EXTRA" in ln)
    raw = line.split(":", 1)[1].strip()
    assert raw.startswith("'") and raw.endswith("'")
    assert "''" in raw, "shlex-quoted args should have escaped inner single quotes"


def test_the_tour_build_id_gate_reaches_the_worker_env_only_when_set():
    # The tour replays read STEPPY_TOUR_EXPECT_BUILD_ID inside the container;
    # unset, the override stays byte-identical.
    assert _render() == _render(tour_expect_build_id="")
    assert "STEPPY_TOUR_EXPECT_BUILD_ID" not in _render()
    assert "STEPPY_TOUR_EXPECT_BUILD_ID: 'rel-42'" in _render(tour_expect_build_id="rel-42")
    assert "STEPPY_TOUR_EXPECT_BUILD_ID: 'it''s'" in _render(tour_expect_build_id="it's")


def test_coord_subdir_redirects_the_queue():
    """What makes the retry pass possible: attempt N reads its own queue."""
    yml = _render(coord_subdir="retry-1")
    assert "STEPPY_COORD_DIR: /steppy/coord/retry-1\n" in yml
    # The mount itself is unchanged — only the worker's view of it moves.
    assert "/tmp/run:/steppy/coord" in yml


def test_auth_mount_overrides_the_shared_auth_dir():
    yml = _render(auth_mount="/tmp/run/auth/w1")
    assert "- /tmp/run/auth/w1:/steppy/project/.auth" in yml
    assert "/proj/.auth:/steppy/project/.auth" not in yml


# ── Worker argv ─────────────────────────────────────────────────────────────


def test_worker_argv_unchanged_without_env():
    """Single-worker parity: no STEPPY_RETRIES / STEPPY_PYTEST_EXTRA ⇒ the
    historic argv (guard: tests/test_run_byte_identical.py)."""
    test_file = Path("tests/e2e/test_login.py")
    argv = _build_pytest_argv(test_file, False, {})
    assert argv[1:] == ["-m", "pytest", str(test_file), "--browser", "chromium"]


def test_worker_argv_adds_reruns(monkeypatch):
    monkeypatch.setattr("steppy.worker._rerunfailures_available", lambda: True)
    argv = _build_pytest_argv(Path("t.py"), True, {"STEPPY_RETRIES": "2"})
    assert "--reruns=2" in argv


def test_worker_argv_skips_reruns_when_plugin_missing(monkeypatch, capsys):
    """Warn and continue — a missing optional plugin must never fail the run
    (mirrors cli._apply_report_args)."""
    monkeypatch.setattr("steppy.worker._rerunfailures_available", lambda: False)
    argv = _build_pytest_argv(Path("t.py"), True, {"STEPPY_RETRIES": "2"})
    assert not any(a.startswith("--reruns") for a in argv)
    assert "pytest-rerunfailures" in capsys.readouterr().err


def test_worker_argv_zero_retries_is_a_noop(monkeypatch):
    monkeypatch.setattr("steppy.worker._rerunfailures_available", lambda: True)
    argv = _build_pytest_argv(Path("t.py"), True, {"STEPPY_RETRIES": "0"})
    assert not any(a.startswith("--reruns") for a in argv)


def test_worker_argv_appends_passthrough():
    argv = _build_pytest_argv(Path("t.py"), True, {"STEPPY_PYTEST_EXTRA": "-k 'login and not slow' --maxfail=2"})
    assert argv[-3:] == ["-k", "login and not slow", "--maxfail=2"]
