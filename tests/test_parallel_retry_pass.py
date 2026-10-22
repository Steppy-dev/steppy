"""Retry pass, FLAKY classification, and the resulting exit code.

The retry pass exists to answer one question the main pass cannot: did this
scenario fail because it is broken, or because eight workers were hammering the
same app? Re-running the failures alone separates the two. These tests pin the
classification, the exit-code policy that follows from it, and the two pieces of
downstream bookkeeping that would otherwise silently regress — which pytest log
gets persisted, and what ``steppy rerun`` is told to re-run.

No Docker: :func:`classify` and friends are pure, and the coordinator-level
tests drive ``_finalize_run`` directly with hand-built PassResults.
"""

from __future__ import annotations

import json
from pathlib import Path

from steppy._parallel.event_bus import EventBus
from steppy._parallel.queue import Queue, ScenarioResult
from steppy._parallel.retry import PassResult, classify, log_roots_by_slug


def _result(slug: str, exit_code: int, worker_id: str = "w1") -> ScenarioResult:
    return ScenarioResult(
        slug=slug,
        worker_id=worker_id,
        exit_code=exit_code,
        duration_s=1.5,
        started_at=0.0,
        finished_at=1.5,
    )


# ── classify ────────────────────────────────────────────────────────────────


def test_single_pass_has_no_flaky():
    """Without a retry there is no evidence of flakiness — everything that
    failed simply failed."""
    v = classify([PassResult(attempt=1, coord_root=Path("/t"), results=[_result("a", 0), _result("b", 1)])])
    assert v.passed == ["a"]
    assert v.flaky == []
    assert v.failed == ["b"]


def test_fail_then_pass_is_flaky():
    passes = [
        PassResult(attempt=1, coord_root=Path("/t"), results=[_result("a", 0), _result("b", 1)]),
        PassResult(attempt=2, coord_root=Path("/t/retry-1"), results=[_result("b", 0)]),
    ]
    v = classify(passes)
    assert v.flaky == ["b"]
    assert v.failed == []
    assert v.passed == ["a"]
    assert v.attempts["b"] == 2


def test_fail_twice_is_failed():
    passes = [
        PassResult(attempt=1, coord_root=Path("/t"), results=[_result("b", 1)]),
        PassResult(attempt=2, coord_root=Path("/t/retry-1"), results=[_result("b", 1)]),
    ]
    v = classify(passes)
    assert v.failed == ["b"]
    assert v.flaky == []
    assert v.any_real_failure


def test_orphan_is_retried_and_can_recover():
    """A worker dying mid-scenario used to be a hard loss ('v0.1: reported as
    failures, not requeued'). Folding orphans into the retry set makes that
    recoverable."""
    passes = [
        PassResult(attempt=1, coord_root=Path("/t"), results=[], orphans={"w2": ["ghost"]}),
        PassResult(attempt=2, coord_root=Path("/t/retry-1"), results=[_result("ghost", 0)]),
    ]
    v = classify(passes)
    assert v.flaky == ["ghost"]
    assert v.failed == []


def test_orphan_never_recovered_stays_failed():
    passes = [PassResult(attempt=1, coord_root=Path("/t"), results=[], orphans={"w2": ["ghost"]})]
    assert classify(passes).failed == ["ghost"]


def test_log_roots_point_at_the_final_attempt():
    """``failures.persist_failure_artifacts`` reads
    ``<root>/logs/<worker>/<slug>.log``; a retried scenario's log lives under
    the retry's coord root, not the main pass's."""
    main = PassResult(attempt=1, coord_root=Path("/t"), results=[_result("a", 0), _result("b", 1)])
    retry = PassResult(attempt=2, coord_root=Path("/t/retry-1"), results=[_result("b", 1)])
    roots = log_roots_by_slug([main, retry])
    assert roots["a"] == Path("/t")
    assert roots["b"] == Path("/t/retry-1")


# ── _finalize_run integration ───────────────────────────────────────────────


def _finalize(tmp_path: Path, main_results, retry_passes):
    """Drive coordinator._finalize_run over a real queue + tmp tree."""
    from rich.console import Console

    from steppy import coordinator

    project_root = tmp_path / "proj"
    tests_dir = project_root / "tests" / "e2e"
    tests_dir.mkdir(parents=True)
    coord = tmp_path / "coord"
    queue = Queue(root=coord)
    queue.seed([r.slug for r in main_results])
    for r in main_results:
        queue.write_result(r)

    return coordinator._finalize_run(
        queue=queue,
        bus=EventBus(),
        console=Console(force_terminal=False, no_color=True),
        tmp_dir=coord,
        project_root=project_root,
        tests_dir=tests_dir,
        retry_passes=retry_passes,
        run_id="testrun",
        workers=2,
    )


def test_finalize_reports_flaky_as_passing_result(tmp_path: Path):
    """The merged result set must reflect the LAST attempt, so a recovered
    scenario stops being counted (and rendered) as a failure."""
    retry = PassResult(attempt=2, coord_root=tmp_path / "coord" / "retry-1", results=[_result("b", 0)])
    results, orphans, verdict = _finalize(tmp_path, [_result("a", 0), _result("b", 1)], [retry])

    assert verdict.flaky == ["b"]
    assert {r.slug: r.exit_code for r in results} == {"a": 0, "b": 0}
    assert orphans == {}


def test_lastfailed_excludes_flaky(tmp_path: Path):
    """``steppy rerun`` must not re-run scenarios that ended green — that would
    burn a rerun on known-good tests and bury the real failures."""
    retry = PassResult(
        attempt=2,
        coord_root=tmp_path / "coord" / "retry-1",
        results=[_result("flaky-one", 0), _result("really-broken", 1)],
    )
    _finalize(tmp_path, [_result("flaky-one", 1), _result("really-broken", 1)], [retry])

    cache = tmp_path / "proj" / ".pytest_cache" / "v" / "cache" / "lastfailed"
    entries = json.loads(cache.read_text(encoding="utf-8"))
    assert any("really-broken" in k for k in entries)
    assert not any("flaky-one" in k for k in entries)


def test_no_retry_pass_keeps_old_lastfailed_behaviour(tmp_path: Path):
    _finalize(tmp_path, [_result("a", 0), _result("b", 1)], None)
    cache = tmp_path / "proj" / ".pytest_cache" / "v" / "cache" / "lastfailed"
    entries = json.loads(cache.read_text(encoding="utf-8"))
    assert len(entries) == 1 and any("b" in k for k in entries)


def test_finalize_without_retry_returns_no_verdict(tmp_path: Path):
    _results, _orphans, verdict = _finalize(tmp_path, [_result("a", 0)], None)
    assert verdict is None


def test_history_is_written(tmp_path: Path):
    retry = PassResult(attempt=2, coord_root=tmp_path / "coord" / "retry-1", results=[_result("b", 0)])
    _finalize(tmp_path, [_result("a", 0), _result("b", 1)], [retry])

    history = tmp_path / "proj" / ".steppy" / "history.jsonl"
    records = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]
    by_slug = {r["slug"]: r for r in records}
    assert by_slug["a"]["outcome"] == "passed"
    assert by_slug["b"]["outcome"] == "flaky"
    assert by_slug["b"]["attempts"] == 2
    assert all(r["run_id"] == "testrun" for r in records)


# ── retry driver ────────────────────────────────────────────────────────────


def test_retry_pass_seeds_only_the_failures(tmp_path: Path, monkeypatch):
    """The retry queue must contain exactly what failed — re-running the whole
    suite would defeat the purpose."""
    from rich.console import Console

    from steppy._parallel import retry as retry_mod

    seeded: dict = {}
    monkeypatch.setattr(retry_mod, "_recreate_worker", lambda **kw: (0, None))
    monkeypatch.setattr(retry_mod, "render_override", lambda **kw: "services: {}\n")

    class _NoopTailer:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(retry_mod, "WorkerLogTailer", _NoopTailer)

    # Patch the Queue class the module under test actually holds, not the one
    # this file imported: tests/test_no_parallel_import_on_default_path.py
    # purges steppy._parallel.* from sys.modules, so a later re-import gives
    # retry_mod a DIFFERENT class object and patching our own would silently
    # do nothing (the spy never fires, the assertion KeyErrors).
    queue_cls = retry_mod.Queue
    real_seed = queue_cls.seed

    def _spy_seed(self, slugs):
        seeded["slugs"] = list(slugs)
        real_seed(self, slugs)

    monkeypatch.setattr(queue_cls, "seed", _spy_seed)

    result = retry_mod.run_retry_pass(
        attempt=2,
        slugs=["b", "c"],
        projects=["proj-w1"],
        compose_file=tmp_path / "docker-compose.yml",
        tmp_dir=tmp_path,
        override_kwargs={},
        console=Console(force_terminal=False, no_color=True),
        verbose=False,
        resolve_container_ids=lambda projects: {p: f"cid-{p}" for p in projects},
        wait_for_workers=lambda ids, console, on_poll=None: [0] * len(ids),
        register_teardown=lambda fn: None,
    )

    assert seeded["slugs"] == ["b", "c"]
    assert result is not None
    assert result.coord_root == tmp_path / "retry-2"


def test_retry_pass_uses_its_own_coord_subdir(tmp_path: Path, monkeypatch):
    """Attempt N reads a private queue so attempt N-1's claims don't block it
    and its evidence survives side by side."""
    from rich.console import Console

    from steppy._parallel import retry as retry_mod

    captured: list[dict] = []
    monkeypatch.setattr(retry_mod, "_recreate_worker", lambda **kw: (0, None))
    monkeypatch.setattr(retry_mod, "render_override", lambda **kw: captured.append(kw) or "services: {}\n")

    class _NoopTailer:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(retry_mod, "WorkerLogTailer", _NoopTailer)

    retry_mod.run_retry_pass(
        attempt=2,
        slugs=["b"],
        projects=["proj-w1", "proj-w2"],
        compose_file=tmp_path / "dc.yml",
        tmp_dir=tmp_path,
        override_kwargs={"locales": "fr"},
        console=Console(force_terminal=False, no_color=True),
        verbose=False,
        resolve_container_ids=lambda projects: {p: f"cid-{p}" for p in projects},
        wait_for_workers=lambda ids, console, on_poll=None: [0] * len(ids),
        register_teardown=lambda fn: None,
    )

    assert len(captured) == 2
    for i, kw in enumerate(captured, start=1):
        assert kw["coord_subdir"] == "retry-2"
        assert kw["worker_index"] == i
        # Settings from the main pass must carry over verbatim.
        assert kw["locales"] == "fr"


def test_retry_pass_returns_none_when_workers_cannot_start(tmp_path: Path, monkeypatch):
    """A retry that can't start must degrade to 'no retry', not crash the run."""
    from rich.console import Console

    from steppy._parallel import retry as retry_mod

    monkeypatch.setattr(retry_mod, "_recreate_worker", lambda **kw: (1, "boom"))
    monkeypatch.setattr(retry_mod, "render_override", lambda **kw: "services: {}\n")

    assert (
        retry_mod.run_retry_pass(
            attempt=2,
            slugs=["b"],
            projects=["proj-w1"],
            compose_file=tmp_path / "dc.yml",
            tmp_dir=tmp_path,
            override_kwargs={},
            console=Console(force_terminal=False, no_color=True),
            verbose=False,
            resolve_container_ids=lambda projects: {},
            wait_for_workers=lambda ids, console, on_poll=None: [],
            register_teardown=lambda fn: None,
        )
        is None
    )
