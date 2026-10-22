"""Tests for ``steppy._parallel.lastfailed.write_lastfailed_cache``.

The unit under test writes pytest's ``lastfailed`` cache from a parallel
run's aggregated results so ``steppy rerun`` re-runs THAT run's failures
(not a stale older single-worker run's).
"""

from __future__ import annotations

import json
from pathlib import Path

from steppy._parallel.event_bus import EventBus
from steppy._parallel.lastfailed import write_lastfailed_cache
from steppy._parallel.queue import ScenarioResult


LASTFAILED_REL = Path(".pytest_cache") / "v" / "cache" / "lastfailed"


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "project"
    p.mkdir()
    return p


def _read_cache(project_root: Path) -> dict:
    return json.loads((project_root / LASTFAILED_REL).read_text(encoding="utf-8"))


def _bus_for_failed_scenario(slug: str, worker_id: str = "w1") -> EventBus:
    """A bus with real per-test events for a failed scenario, as a worker
    running ``pytest tests/e2e/test_<slug>.py`` would emit."""
    bus = EventBus()
    nodeid = f"tests/e2e/test_{slug}.py::test_{slug}"
    bus.feed_line(worker_id, f"STEPPY-EVT::scenario-start::{slug}::{worker_id}::1.0")
    bus.feed_line(worker_id, f"STEPPY-EVT::test-start::{nodeid}::1.0")
    bus.feed_line(worker_id, f"STEPPY-EVT::test-end::{nodeid}::failed::3000")
    return bus


def test_all_pass_writes_empty_dict_and_overwrites_stale(tmp_path):
    """Core regression: an all-pass parallel run must RESET the cache to {},
    overwriting whatever a prior (single-worker) run left behind — otherwise
    `steppy rerun` re-runs an OLD failure set."""
    project_root = _project(tmp_path)
    # Pre-seed a stale cache from a hypothetical earlier run.
    stale = project_root / LASTFAILED_REL
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps({"tests/e2e/test_old.py::test_old": True}), encoding="utf-8")

    out = write_lastfailed_cache(
        project_root=project_root,
        tests_dir=Path("tests/e2e"),
        scenario_results=[ScenarioResult("checkout", "w1", 0, 1.0, 0.0, 1.0)],
        orphans={},
        bus=EventBus(),
    )

    assert out == stale
    assert _read_cache(project_root) == {}


def test_failed_scenario_uses_bus_derived_file_path(tmp_path):
    project_root = _project(tmp_path)
    out = write_lastfailed_cache(
        project_root=project_root,
        tests_dir=Path("tests/e2e"),
        scenario_results=[ScenarioResult("checkout", "w1", 1, 3.0, 0.0, 3.0)],
        orphans={},
        bus=_bus_for_failed_scenario("checkout"),
    )

    assert out is not None
    # File-level key (no ``::``), spelled exactly as the worker's pytest did.
    assert _read_cache(project_root) == {"tests/e2e/test_checkout.py": True}


def test_orphan_without_bus_record_falls_back_to_synthesized_key(tmp_path):
    """A worker that died before reporting leaves no bus test record; the key
    is synthesized from tests_dir + slug so the scenario is still re-run."""
    project_root = _project(tmp_path)
    out = write_lastfailed_cache(
        project_root=project_root,
        tests_dir=Path("tests/e2e"),
        scenario_results=[],
        orphans={"w2": ["crashed"]},
        bus=EventBus(),
    )

    assert out is not None
    assert _read_cache(project_root) == {"tests/e2e/test_crashed.py": True}


def test_synthetic_nodeid_is_ignored_and_falls_back(tmp_path):
    """When the events plugin didn't load, the bus self-heals a scenario-end
    into a ``<synthetic>::slug`` record. That's not a real file — the writer
    must skip it and synthesize a proper key."""
    project_root = _project(tmp_path)
    bus = EventBus()
    bus.feed_line("w1", "STEPPY-EVT::scenario-start::brokenplugin::w1::1.0")
    # No test-start/test-end → scenario-end self-heals to a synthetic nodeid.
    bus.feed_line("w1", "STEPPY-EVT::scenario-end::brokenplugin::w1::1::2000")

    out = write_lastfailed_cache(
        project_root=project_root,
        tests_dir=Path("tests/e2e"),
        scenario_results=[ScenarioResult("brokenplugin", "w1", 1, 2.0, 0.0, 2.0)],
        orphans={},
        bus=bus,
    )

    assert out is not None
    assert _read_cache(project_root) == {"tests/e2e/test_brokenplugin.py": True}


def test_mixed_pass_and_fail_only_writes_failures(tmp_path):
    project_root = _project(tmp_path)
    bus = _bus_for_failed_scenario("checkout")
    out = write_lastfailed_cache(
        project_root=project_root,
        tests_dir=Path("tests/e2e"),
        scenario_results=[
            ScenarioResult("login", "w1", 0, 1.0, 0.0, 1.0),
            ScenarioResult("checkout", "w1", 1, 3.0, 0.0, 3.0),
        ],
        orphans={},
        bus=bus,
    )

    assert out is not None
    assert _read_cache(project_root) == {"tests/e2e/test_checkout.py": True}


def test_non_default_tests_dir_prefixes_synthesized_key(tmp_path):
    project_root = _project(tmp_path)
    out = write_lastfailed_cache(
        project_root=project_root,
        tests_dir=Path("e2e"),
        scenario_results=[],
        orphans={"w1": ["crashed"]},
        bus=EventBus(),
    )

    assert out is not None
    assert _read_cache(project_root) == {"e2e/test_crashed.py": True}


def test_writes_gitignore_to_keep_cache_out_of_git(tmp_path):
    project_root = _project(tmp_path)
    write_lastfailed_cache(
        project_root=project_root,
        tests_dir=Path("tests/e2e"),
        scenario_results=[ScenarioResult("checkout", "w1", 1, 3.0, 0.0, 3.0)],
        orphans={},
        bus=_bus_for_failed_scenario("checkout"),
    )

    gitignore = project_root / ".pytest_cache" / ".gitignore"
    assert gitignore.exists()
    assert "*" in gitignore.read_text(encoding="utf-8")


def test_absolute_tests_dir_under_project_is_made_relative(tmp_path):
    project_root = _project(tmp_path)
    abs_tests = project_root / "tests" / "e2e"
    out = write_lastfailed_cache(
        project_root=project_root,
        tests_dir=abs_tests,
        scenario_results=[],
        orphans={"w1": ["crashed"]},
        bus=EventBus(),
    )

    assert out is not None
    assert _read_cache(project_root) == {"tests/e2e/test_crashed.py": True}
