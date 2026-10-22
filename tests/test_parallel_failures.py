"""Tests for ``steppy._parallel.failures.persist_failure_artifacts``.

The unit under test synthesizes a tmp_dir (queue + per-scenario logs +
per-worker stack logs) and asserts the on-disk artifacts under
``output/failures/`` are written correctly.
"""

from __future__ import annotations

import base64
from pathlib import Path

from steppy._parallel.event_bus import EventBus
from steppy._parallel.failures import (
    WORKER_LOGS_DIRNAME,
    persist_failure_artifacts,
)
from steppy._parallel.queue import ScenarioResult


def _make_tmp_dir(tmp_path: Path) -> Path:
    """Build a tmp_dir shaped like the coordinator's: logs/<wid>/<slug>.log,
    <wid>-stack.log. Returns the root."""
    d = tmp_path / "coord"
    d.mkdir()
    (d / "logs").mkdir()
    return d


def _write_pytest_log(tmp_dir: Path, worker_id: str, slug: str, body: str) -> None:
    log_dir = tmp_dir / "logs" / worker_id
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{slug}.log").write_text(body, encoding="utf-8")


def _write_stack_log(tmp_dir: Path, worker_id: str, body: str) -> None:
    (tmp_dir / f"{worker_id}-stack.log").write_text(body, encoding="utf-8")


def test_no_failures_returns_none_and_writes_nothing(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    tmp_dir = _make_tmp_dir(tmp_path)
    bus = EventBus()
    out = persist_failure_artifacts(
        project_root=project_root,
        tmp_dir=tmp_dir,
        bus=bus,
        scenario_results=[ScenarioResult("a", "w1", 0, 1.0, 0.0, 1.0)],
        orphans={},
    )
    assert out is None
    assert not (project_root / "output" / "failures").exists()


def test_failed_scenario_writes_pytest_log_and_summary(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    tmp_dir = _make_tmp_dir(tmp_path)
    _write_pytest_log(tmp_dir, "w1", "checkout", "FAILED test_checkout\nAssertionError: nope\n")
    _write_stack_log(tmp_dir, "w1", "<entire docker compose logs blob>\n")
    bus = EventBus()
    bus.feed_line("w1", "STEPPY-EVT::scenario-start::checkout::w1::1.0")
    bus.feed_line("w1", "STEPPY-EVT::test-start::tests/e2e/test_checkout.py::test_checkout::1.0")
    bus.feed_line("w1", "STEPPY-EVT::test-end::tests/e2e/test_checkout.py::test_checkout::failed::3000")
    tail = "E   AssertionError: nope"
    b64 = base64.b64encode(tail.encode()).decode()
    bus.feed_line("w1", f"STEPPY-EVT::test-fail-tail::tests/e2e/test_checkout.py::test_checkout::{b64}")

    out = persist_failure_artifacts(
        project_root=project_root,
        tmp_dir=tmp_dir,
        bus=bus,
        scenario_results=[ScenarioResult("checkout", "w1", 1, 3.0, 0.0, 3.0)],
        orphans={},
    )

    assert out is not None
    scen_dir = project_root / "output" / "failures" / "checkout"
    assert scen_dir.is_dir()
    assert (scen_dir / "pytest.log").read_text(encoding="utf-8").startswith("FAILED test_checkout")

    summary = (scen_dir / "summary.txt").read_text(encoding="utf-8")
    assert "scenario: checkout" in summary
    assert "worker:   w1" in summary
    assert "FAILED (exit=1)" in summary
    assert "AssertionError: nope" in summary

    stack_log_copy = project_root / "output" / "failures" / WORKER_LOGS_DIRNAME / "w1-stack.log"
    assert stack_log_copy.exists()
    assert "docker compose logs" in stack_log_copy.read_text(encoding="utf-8")


def test_orphan_writes_summary_even_without_pytest_log(tmp_path):
    """Orphan = worker died before writing a result. The pytest.log may
    or may not exist — summary.txt must be written regardless with the
    ORPHAN marker."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    tmp_dir = _make_tmp_dir(tmp_path)
    # No pytest log written — simulate worker dying before pytest spawned.
    bus = EventBus()
    out = persist_failure_artifacts(
        project_root=project_root,
        tmp_dir=tmp_dir,
        bus=bus,
        scenario_results=[],
        orphans={"w2": ["abandoned"]},
    )
    assert out is not None
    summary_path = project_root / "output" / "failures" / "abandoned" / "summary.txt"
    assert summary_path.exists()
    summary = summary_path.read_text(encoding="utf-8")
    assert "ORPHAN" in summary
    assert "worker:   w2" in summary
    assert not (project_root / "output" / "failures" / "abandoned" / "pytest.log").exists()


def test_existing_failures_dir_is_wiped(tmp_path):
    """A failures/ dir from a previous run must not leak stale entries."""
    project_root = tmp_path / "project"
    (project_root / "output" / "failures" / "old-scenario").mkdir(parents=True)
    (project_root / "output" / "failures" / "old-scenario" / "stale.txt").write_text("x")
    tmp_dir = _make_tmp_dir(tmp_path)
    _write_pytest_log(tmp_dir, "w1", "new-failure", "log\n")
    bus = EventBus()

    persist_failure_artifacts(
        project_root=project_root,
        tmp_dir=tmp_dir,
        bus=bus,
        scenario_results=[ScenarioResult("new-failure", "w1", 1, 1.0, 0.0, 1.0)],
        orphans={},
    )

    assert not (project_root / "output" / "failures" / "old-scenario").exists()
    assert (project_root / "output" / "failures" / "new-failure").exists()


def test_worker_stack_log_copied_once_per_worker(tmp_path):
    """Two failures on the same worker → one stack log copy, not two."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    tmp_dir = _make_tmp_dir(tmp_path)
    _write_pytest_log(tmp_dir, "w1", "fail-a", "a\n")
    _write_pytest_log(tmp_dir, "w1", "fail-b", "b\n")
    _write_stack_log(tmp_dir, "w1", "shared stack log\n")
    bus = EventBus()

    out = persist_failure_artifacts(
        project_root=project_root,
        tmp_dir=tmp_dir,
        bus=bus,
        scenario_results=[
            ScenarioResult("fail-a", "w1", 1, 1.0, 0.0, 1.0),
            ScenarioResult("fail-b", "w1", 2, 1.0, 0.0, 1.0),
        ],
        orphans={},
    )

    assert out is not None
    assert set(out.scenario_dirs.keys()) == {"fail-a", "fail-b"}
    assert set(out.worker_logs.keys()) == {"w1"}
    stack_dir = project_root / "output" / "failures" / WORKER_LOGS_DIRNAME
    assert sorted(p.name for p in stack_dir.iterdir()) == ["w1-stack.log"]


def test_failure_screenshots_referenced_in_summary(tmp_path):
    """Failure screenshots discovered under output/screenshots/_failures/
    that mention the slug should be listed in summary.txt."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    fail_shots = project_root / "output" / "screenshots" / "_failures"
    fail_shots.mkdir(parents=True)
    matching = fail_shots / "test_login__editor_page.png"
    matching.write_bytes(b"\x89PNG")
    unrelated = fail_shots / "test_checkout__admin_page.png"
    unrelated.write_bytes(b"\x89PNG")
    tmp_dir = _make_tmp_dir(tmp_path)
    _write_pytest_log(tmp_dir, "w1", "login", "log\n")
    bus = EventBus()

    persist_failure_artifacts(
        project_root=project_root,
        tmp_dir=tmp_dir,
        bus=bus,
        scenario_results=[ScenarioResult("login", "w1", 1, 1.0, 0.0, 1.0)],
        orphans={},
    )

    summary = (project_root / "output" / "failures" / "login" / "summary.txt").read_text(encoding="utf-8")
    assert "test_login__editor_page.png" in summary
    assert "test_checkout__admin_page.png" not in summary


def test_orphan_after_failed_result_does_not_overwrite(tmp_path):
    """If a slug shows up both as a failed ScenarioResult and as an orphan
    (rare race), the failed-result branch wins and the summary keeps the
    real exit code, not ORPHAN."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    tmp_dir = _make_tmp_dir(tmp_path)
    _write_pytest_log(tmp_dir, "w1", "racy", "log\n")
    bus = EventBus()
    persist_failure_artifacts(
        project_root=project_root,
        tmp_dir=tmp_dir,
        bus=bus,
        scenario_results=[ScenarioResult("racy", "w1", 1, 1.0, 0.0, 1.0)],
        orphans={"w1": ["racy"]},
    )
    summary = (project_root / "output" / "failures" / "racy" / "summary.txt").read_text(encoding="utf-8")
    assert "FAILED (exit=1)" in summary
    assert "ORPHAN" not in summary
