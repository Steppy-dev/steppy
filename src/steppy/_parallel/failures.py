"""Persist per-scenario failure artifacts to ``output/failures/`` so they
survive the coordinator's ``tmp_dir`` teardown.

In ``steppy run --workers N >= 2`` the coordinator runs scenarios inside
Docker workers and tears the run dir down at exit. Without this module,
when a scenario fails the user is left with at most a 15-line longrepr
tail printed in the run summary — the per-worker stack log and every
pytest line are wiped by ``shutil.rmtree(tmp_dir)`` moments later.

What this module copies (only when there were failures or orphans):

    output/failures/
        <slug>/
            pytest.log    # per-scenario pytest stdout (from worker.py's tee)
            summary.txt   # human-readable: exit code, worker, fail-tails, screenshots
        _worker-logs/
            wN-stack.log  # full ``docker compose logs`` for the affected worker

Lazy-imported from :mod:`steppy.coordinator` so it only loads in
parallel mode (the single-worker path's ``test_no_parallel_import_on_default_path``
guard relies on this).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .event_bus import EventBus
from .queue import ScenarioResult


FAILURES_DIRNAME = "failures"
WORKER_LOGS_DIRNAME = "_worker-logs"


@dataclass(frozen=True)
class PersistedArtifacts:
    """What ``persist_failure_artifacts`` actually wrote. Returned so the
    renderer can confidently reference the paths and tests can assert."""

    failures_dir: Path
    scenario_dirs: dict[str, Path]  # slug -> output/failures/<slug>/
    worker_logs: dict[str, Path]  # worker_id -> output/failures/_worker-logs/<wid>-stack.log


def persist_failure_artifacts(
    *,
    project_root: Path,
    tmp_dir: Path,
    bus: EventBus,
    scenario_results: list[ScenarioResult],
    orphans: dict[str, list[str]],
    log_roots: Optional[dict[str, Path]] = None,
    screenshots_dir: Optional[Path] = None,
) -> Optional[PersistedArtifacts]:
    """Copy per-scenario logs and write human-readable summaries.

    Returns the :class:`PersistedArtifacts` describing what was written,
    or ``None`` when there is nothing to persist (no failures, no orphans).

    ``screenshots_dir`` is the capture root the generated conftest wrote to
    (``config.output.screenshots_dir``, resolved by the coordinator). This
    function is a *reader* of that tree, so it has to follow the same knob or
    it silently harvests nothing. Omitted ⇒ the conventional
    ``<project>/output/screenshots``.

    ``log_roots`` maps a slug to the coord root of the attempt that produced
    its final outcome. It exists for the retry pass: attempt 2 writes its
    per-scenario pytest log under ``<tmp>/retry-1/logs/...``, so without the
    mapping we would copy attempt 1's stale log (or none at all). Omitted ⇒
    every slug resolves against ``tmp_dir``, i.e. the pre-retry behaviour.

    The function NEVER raises — artifact persistence is best-effort. Any
    OSError on the host filesystem is silently swallowed since the run
    itself has already produced a meaningful exit code and summary; we
    don't want a stat() race on a teardown to crash the coordinator.
    """
    log_roots = log_roots or {}
    failed_results = [r for r in scenario_results if r.exit_code != 0]
    orphan_pairs: list[tuple[str, str]] = [(worker_id, slug) for worker_id, slugs in orphans.items() for slug in slugs]
    if not failed_results and not orphan_pairs:
        return None

    failures_dir = project_root / "output" / FAILURES_DIRNAME
    try:
        _reset_dir(failures_dir)
    except OSError:
        return None

    # Build the fail-tail lookup once. Mirrors renderer._build_failure_rows.
    snap = bus.snapshot()
    tails_by_scenario: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for t in snap.tests:
        if t.outcome in ("failed", "error", "worker_died") and t.fail_tail:
            tails_by_scenario.setdefault((t.worker_id, t.scenario_slug), []).append((t.nodeid, t.fail_tail))

    # Discover failure screenshots (the generated conftest writes here).
    failure_screenshots_dir = (screenshots_dir or project_root / "output" / "screenshots") / "_failures"
    failure_screenshots: list[Path] = []
    if failure_screenshots_dir.exists():
        try:
            failure_screenshots = sorted(failure_screenshots_dir.glob("*.png"))
        except OSError:
            failure_screenshots = []

    scenario_dirs: dict[str, Path] = {}
    workers_with_failures: set[str] = set()

    # Failed scenarios.
    for r in failed_results:
        scenario_dir = failures_dir / r.slug
        scenario_dir.mkdir(parents=True, exist_ok=True)
        _copy_pytest_log(log_roots.get(r.slug, tmp_dir), r.worker_id, r.slug, scenario_dir)
        screenshots = _screenshots_for_slug(r.slug, failure_screenshots)
        _write_summary(
            scenario_dir / "summary.txt",
            slug=r.slug,
            worker_id=r.worker_id,
            exit_code=r.exit_code,
            duration_s=r.duration_s,
            started_at=r.started_at,
            tails=tails_by_scenario.get((r.worker_id, r.slug), []),
            screenshots=screenshots,
            orphan=False,
        )
        scenario_dirs[r.slug] = scenario_dir
        workers_with_failures.add(r.worker_id)

    # Orphans (claimed but never reported). pytest.log may exist (worker
    # died mid-run) or not (worker died before spawning pytest). Either way,
    # emit a summary.txt with the ORPHAN marker.
    for worker_id, slug in orphan_pairs:
        if slug in scenario_dirs:
            # A failed result already exists for this slug (race between
            # write_result and crash). Don't double-write.
            continue
        scenario_dir = failures_dir / slug
        scenario_dir.mkdir(parents=True, exist_ok=True)
        _copy_pytest_log(log_roots.get(slug, tmp_dir), worker_id, slug, scenario_dir)
        screenshots = _screenshots_for_slug(slug, failure_screenshots)
        _write_summary(
            scenario_dir / "summary.txt",
            slug=slug,
            worker_id=worker_id,
            exit_code=None,
            duration_s=None,
            started_at=None,
            tails=tails_by_scenario.get((worker_id, slug), []),
            screenshots=screenshots,
            orphan=True,
        )
        scenario_dirs[slug] = scenario_dir
        workers_with_failures.add(worker_id)

    # Per-worker stack logs (one copy per affected worker, not per failure).
    worker_logs_dir = failures_dir / WORKER_LOGS_DIRNAME
    worker_logs: dict[str, Path] = {}
    if workers_with_failures:
        try:
            worker_logs_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            worker_logs_dir = None  # type: ignore[assignment]
        if worker_logs_dir is not None:
            for worker_id in sorted(workers_with_failures):
                src = tmp_dir / f"{worker_id}-stack.log"
                if not src.exists():
                    continue
                dst = worker_logs_dir / f"{worker_id}-stack.log"
                try:
                    shutil.copy2(src, dst)
                except OSError:
                    continue
                worker_logs[worker_id] = dst

    return PersistedArtifacts(
        failures_dir=failures_dir,
        scenario_dirs=scenario_dirs,
        worker_logs=worker_logs,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────


def _reset_dir(d: Path) -> None:
    """Remove ``d`` (recursively) if it exists, then recreate it empty.
    Prevents stale failures from a prior run leaking into this one."""
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)


def _copy_pytest_log(coord_root: Path, worker_id: str, slug: str, scenario_dir: Path) -> None:
    """``coord_root`` is the coord dir of the attempt that produced this
    scenario's final outcome — ``tmp_dir`` for a single-pass run, or
    ``tmp_dir/retry-N`` when the retry pass ran it last."""
    src = coord_root / "logs" / worker_id / f"{slug}.log"
    if not src.exists():
        return
    try:
        shutil.copy2(src, scenario_dir / "pytest.log")
    except OSError:
        pass


def _screenshots_for_slug(slug: str, all_screenshots: list[Path]) -> list[Path]:
    """Filter the list of failure screenshots to those that match this slug.

    The generated conftest names files ``{test_id}__{fixture_name}.png``
    where ``test_id`` derives from the pytest nodeid which itself derives
    from ``test_{slug}.py``. Match by substring on the slug to keep this
    robust to parametrized variants (``test_login[editor-fr]``)."""
    return [p for p in all_screenshots if slug in p.name]


def _write_summary(
    path: Path,
    *,
    slug: str,
    worker_id: str,
    exit_code: Optional[int],
    duration_s: Optional[float],
    started_at: Optional[float],
    tails: list[tuple[str, str]],
    screenshots: list[Path],
    orphan: bool,
) -> None:
    """Write a human-readable summary.txt next to pytest.log."""
    lines: list[str] = []
    lines.append(f"scenario: {slug}")
    lines.append(f"worker:   {worker_id}")
    if orphan:
        lines.append("status:   ORPHAN — worker died mid-scenario")
    else:
        lines.append(f"status:   FAILED (exit={exit_code})")
    if duration_s is not None:
        lines.append(f"duration: {duration_s:.2f}s")
    if started_at is not None:
        lines.append(f"started:  {started_at:.3f} (unix)")
    lines.append("")
    if tails:
        lines.append("Failure tails (per test):")
        for nodeid, tail in tails:
            lines.append(f"  • {nodeid}")
            for tail_line in tail.splitlines():
                lines.append(f"      {tail_line}")
            lines.append("")
    else:
        if orphan:
            lines.append("No per-test fail-tail captured (worker exited before reporting).")
        else:
            lines.append("No per-test fail-tail captured (pytest exited non-zero without per-test events).")
        lines.append("")
    if screenshots:
        lines.append("Failure screenshots:")
        for shot in screenshots:
            lines.append(f"  • {shot}")
        lines.append("")
    lines.append("See pytest.log (this directory) for full pytest output.")
    lines.append("See ../_worker-logs/ for the full worker stack log (worker + app).")
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
