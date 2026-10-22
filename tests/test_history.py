"""Run history: the memory that makes ``steppy flaky`` possible.

A single run tells you what broke today. History tells you that one scenario
has been failing intermittently for three weeks while everything around it is
stable — which is the thing that actually directs a fix.

Two properties get the most attention here, because both are the kind of bug
you would only notice much later:

  * the file lives at ``.steppy/`` and **not** under ``output/``, which cleanup
    wipes — history that evaporates on a clean build is worse than none,
    because it still looks authoritative;
  * reads survive a corrupt tail. A killed process leaves a half-written line,
    and a command that dies on it is a command nobody trusts.
"""

from __future__ import annotations

import json
from pathlib import Path

from steppy._parallel.queue import ScenarioResult
from steppy._parallel.retry import PassResult, classify
from steppy.history import (
    append_run,
    flake_stats,
    history_path,
    load,
    median_durations,
    order_by_duration_desc,
    prune,
)


def _result(slug, exit_code=0, duration=1.0, worker="w1"):
    return ScenarioResult(
        slug=slug,
        worker_id=worker,
        exit_code=exit_code,
        duration_s=duration,
        started_at=0.0,
        finished_at=duration,
    )


def _record_run(root: Path, run_id: str, main: list, retry: list | None = None):
    passes = [PassResult(attempt=1, coord_root=root, results=main)]
    if retry is not None:
        passes.append(PassResult(attempt=2, coord_root=root / "retry-1", results=retry))
    verdict = classify(passes) if retry is not None else None
    return append_run(project_root=root, run_id=run_id, workers=4, passes=passes, verdict=verdict)


# ── location & durability ───────────────────────────────────────────────────


def test_history_lives_outside_output(tmp_path):
    """`output/` is wiped by cleanup; history must survive that."""
    _record_run(tmp_path, "r1", [_result("a")])
    assert history_path(tmp_path) == tmp_path / ".steppy" / "history.jsonl"
    assert history_path(tmp_path).is_file()
    assert not (tmp_path / "output").exists()


def test_history_dir_is_gitignored(tmp_path):
    _record_run(tmp_path, "r1", [_result("a")])
    assert (tmp_path / ".steppy" / ".gitignore").is_file()


def test_appends_rather_than_overwrites(tmp_path):
    _record_run(tmp_path, "r1", [_result("a")])
    _record_run(tmp_path, "r2", [_result("a")])
    assert len({r["run_id"] for r in load(tmp_path)}) == 2


def test_append_never_raises_on_bad_input(tmp_path):
    """Telemetry must not be able to fail a run that already succeeded."""
    assert append_run(project_root=tmp_path, run_id="r", workers=1, passes=None) is None


def test_load_skips_corrupt_and_foreign_lines(tmp_path):
    _record_run(tmp_path, "r1", [_result("a")])
    with history_path(tmp_path).open("a", encoding="utf-8") as f:
        f.write("{not json\n")  # torn write from a killed process
        f.write(json.dumps({"v": 999, "slug": "from-the-future"}) + "\n")
    records = load(tmp_path)
    assert [r["slug"] for r in records] == ["a"]


def test_load_of_missing_file_is_empty(tmp_path):
    assert load(tmp_path) == []


# ── outcomes ────────────────────────────────────────────────────────────────


def test_outcomes_reflect_the_verdict(tmp_path):
    _record_run(
        tmp_path,
        "r1",
        [_result("clean"), _result("wobbly", 1), _result("broken", 1)],
        retry=[_result("wobbly", 0), _result("broken", 1)],
    )
    by_slug = {r["slug"]: r for r in load(tmp_path)}
    assert by_slug["clean"]["outcome"] == "passed"
    assert by_slug["wobbly"]["outcome"] == "flaky"
    assert by_slug["broken"]["outcome"] == "failed"


def test_without_verdict_outcomes_are_pass_fail(tmp_path):
    _record_run(tmp_path, "r1", [_result("a"), _result("b", 1)])
    by_slug = {r["slug"]: r["outcome"] for r in load(tmp_path)}
    assert by_slug == {"a": "passed", "b": "failed"}


def test_last_runs_window(tmp_path):
    for i in range(5):
        _record_run(tmp_path, f"r{i}", [_result("a")])
    assert len({r["run_id"] for r in load(tmp_path, last_runs=2)}) == 2


# ── stats ───────────────────────────────────────────────────────────────────


def test_flake_stats_rank_worst_first(tmp_path):
    for i in range(10):
        _record_run(
            tmp_path,
            f"r{i}",
            [_result("stable"), _result("wobbly", 1 if i < 7 else 0)],
            retry=[_result("wobbly", 0)] if i < 7 else [],
        )
    stats = flake_stats(load(tmp_path))
    assert stats[0].slug == "wobbly"
    assert stats[0].flakes == 7
    assert stats[0].flake_rate == 0.7
    assert stats[-1].slug == "stable"
    assert stats[-1].flake_rate == 0.0


def test_flake_rate_counts_failures_and_flakes_alike(tmp_path):
    """Both cost the reader attention; a scenario that fails outright half the
    time is not healthier than one that flakes half the time."""
    _record_run(tmp_path, "r1", [_result("x", 1)])  # failed
    _record_run(tmp_path, "r2", [_result("x", 1)], retry=[_result("x", 0)])  # flaky
    _record_run(tmp_path, "r3", [_result("x")])  # passed
    _record_run(tmp_path, "r4", [_result("x")])  # passed
    stat = flake_stats(load(tmp_path))[0]
    assert (stat.failures, stat.flakes) == (1, 1)
    assert stat.flake_rate == 0.5


def test_durations_are_summarised(tmp_path):
    for i, d in enumerate([1.0, 5.0, 9.0]):
        _record_run(tmp_path, f"r{i}", [_result("a", duration=d)])
    stat = flake_stats(load(tmp_path))[0]
    assert stat.median_s == 5.0
    assert stat.p95_s == 9.0


# ── LPT ordering ────────────────────────────────────────────────────────────


def test_median_durations_and_ordering(tmp_path):
    _record_run(tmp_path, "r1", [_result("quick", duration=2.0), _result("slow", duration=90.0)])
    durations = median_durations(tmp_path)
    assert durations == {"quick": 2.0, "slow": 90.0}
    assert order_by_duration_desc(["quick", "slow"], durations) == ["slow", "quick"]


def test_ordering_puts_unknown_scenarios_last(tmp_path):
    """An unrecorded scenario is most likely newly added; guessing it is slow
    would push a genuinely slow known one behind it."""
    durations = {"slow": 90.0, "quick": 2.0}
    assert order_by_duration_desc(["quick", "brand-new", "slow"], durations) == ["slow", "quick", "brand-new"]


def test_ordering_without_history_is_a_noop():
    slugs = ["c", "a", "b"]
    assert order_by_duration_desc(slugs, {}) == slugs


def test_median_durations_empty_without_history(tmp_path):
    assert median_durations(tmp_path) == {}


# ── prune ───────────────────────────────────────────────────────────────────


def test_prune_keeps_the_newest_runs(tmp_path):
    for i in range(6):
        _record_run(tmp_path, f"r{i}", [_result("a")])
    dropped = prune(tmp_path, keep_runs=2)
    assert dropped == 4
    assert {r["run_id"] for r in load(tmp_path)} == {"r4", "r5"}


def test_prune_is_a_noop_when_nothing_to_drop(tmp_path):
    _record_run(tmp_path, "r1", [_result("a")])
    assert prune(tmp_path, keep_runs=10) == 0
    assert len(load(tmp_path)) == 1


def test_prune_without_history_is_safe(tmp_path):
    assert prune(tmp_path, keep_runs=5) == 0
