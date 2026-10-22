"""Append-only run history — the substrate for ``steppy flaky``.

A single run's summary tells you what broke today. It cannot tell you that
``checkout-with-coupon`` has failed in seven of the last twenty runs while
everything around it is stable. That ranking is what actually directs a fix,
and it needs one thing the codebase did not have: memory.

Storage is ``.steppy/history.jsonl`` — one JSON object per (run, scenario),
appended, never rewritten. Deliberately **not** under ``output/``: that tree is
wiped by cleanup, and history that evaporates on a clean build is worse than no
history because it looks authoritative. This mirrors the coverage store's
reasoning for living at ``.steppy/coverage.db``.

JSONL rather than sqlite because the access pattern is "read the tail, group in
memory" — a few thousand lines is nothing, and a text file survives partial
writes, concurrent appends and manual inspection in a way a binary store does
not.

This is a leaf module: stdlib only, no imports from ``cli``, ``coordinator`` or
``_parallel``. ``steppy.flaky_cli`` and the coordinator both use it, and the
no-parallel-import guard depends on it staying that way.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


HISTORY_DIRNAME = ".steppy"
HISTORY_FILENAME = "history.jsonl"

# Bumped if the record shape changes incompatibly. Readers skip records whose
# version they don't understand rather than crashing on an old file.
RECORD_SCHEMA_VERSION = 1

# Outcomes a record can carry.
OUTCOME_PASSED = "passed"
OUTCOME_FLAKY = "flaky"
OUTCOME_FAILED = "failed"


def history_path(project_root: Path) -> Path:
    return Path(project_root) / HISTORY_DIRNAME / HISTORY_FILENAME


# ── Writing ─────────────────────────────────────────────────────────────────


def append_run(
    *,
    project_root: Path,
    run_id: str,
    workers: int,
    passes: Iterable[Any],
    verdict: Optional[Any] = None,
    bus: Optional[Any] = None,
    started_at: Optional[float] = None,
) -> Optional[Path]:
    """Append one record per scenario for a just-finished parallel run.

    ``passes`` are the :class:`~steppy._parallel.retry.PassResult` objects
    (attempt 1 first). ``verdict`` is the classification when a retry pass ran;
    without one every scenario is simply passed/failed.

    Returns the path written, or ``None`` on any failure — history is
    telemetry, and the caller's run has already produced its real outcome. This
    function never raises.
    """
    try:
        passes = list(passes)
        records = _build_records(
            run_id=run_id,
            workers=workers,
            passes=passes,
            verdict=verdict,
            bus=bus,
            started_at=started_at,
        )
        if not records:
            return None
        path = history_path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        # One open/append per run keeps the write atomic enough for our
        # purposes: concurrent runs interleave whole lines, never partial ones.
        with path.open("a", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, sort_keys=True) + "\n")
        _ensure_gitignored(path.parent)
        return path
    except (OSError, TypeError, ValueError, AttributeError):
        return None


def _build_records(*, run_id, workers, passes, verdict, bus, started_at) -> list[dict]:
    """One record per scenario, reflecting its FINAL outcome across attempts."""
    if not passes:
        return []
    now = time.time()
    started = started_at
    if started is None and bus is not None:
        started = getattr(bus.snapshot(), "wall_clock_started", None)
    started = started if started is not None else now

    flaky = set(getattr(verdict, "flaky", ()) or ())
    failed = set(getattr(verdict, "failed", ()) or ())

    # Last attempt wins for the per-scenario metadata (worker, duration).
    last_result: dict[str, Any] = {}
    attempts_used: dict[str, int] = {}
    for p in passes:
        for r in p.results:
            last_result[r.slug] = r
            attempts_used[r.slug] = p.attempt
        for slugs in p.orphans.values():
            for slug in slugs:
                attempts_used.setdefault(slug, p.attempt)
                last_result.setdefault(slug, None)

    fingerprints = _fingerprints_by_slug(passes)

    records: list[dict] = []
    for slug in sorted(last_result):
        result = last_result[slug]
        if verdict is not None:
            if slug in failed:
                outcome = OUTCOME_FAILED
            elif slug in flaky:
                outcome = OUTCOME_FLAKY
            else:
                outcome = OUTCOME_PASSED
        else:
            outcome = OUTCOME_PASSED if (result is not None and result.exit_code == 0) else OUTCOME_FAILED
        records.append(
            {
                "v": RECORD_SCHEMA_VERSION,
                "run_id": run_id,
                "started_at": round(started, 3),
                "mode": "parallel",
                "workers": workers,
                "slug": slug,
                "outcome": outcome,
                "attempts": attempts_used.get(slug, 1),
                "duration_s": round(result.duration_s, 3) if result is not None else None,
                "worker_id": result.worker_id if result is not None else None,
                "fingerprint": fingerprints.get(slug),
            }
        )
    return records


def _fingerprints_by_slug(passes) -> dict[str, str]:
    """Failure fingerprint per scenario, from the last attempt that produced a
    tail. Lets ``steppy flaky`` say "these five always fail the same way"."""
    try:
        from ._parallel.fingerprint import fingerprint
    except ImportError:
        return {}
    out: dict[str, str] = {}
    for p in passes:
        bus = getattr(p, "bus", None)
        if bus is None:
            continue
        try:
            tests = bus.snapshot().tests
        except AttributeError:
            continue
        for t in tests:
            if t.outcome in ("failed", "error", "worker_died") and t.fail_tail and t.scenario_slug:
                out[t.scenario_slug] = fingerprint(t.fail_tail)
    return out


def _ensure_gitignored(steppy_dir: Path) -> None:
    """``.steppy/`` is already gitignored in scaffolded projects; add a local
    guard for projects that predate it so history never lands in a commit."""
    marker = steppy_dir / ".gitignore"
    if marker.exists():
        return
    try:
        marker.write_text("# Created by steppy automatically.\n*\n", encoding="utf-8")
    except OSError:
        pass


# ── Reading ─────────────────────────────────────────────────────────────────


def load(project_root: Path, *, last_runs: Optional[int] = None) -> list[dict]:
    """Read history records, oldest first.

    ``last_runs`` keeps only the N most recent runs (by first appearance), which
    is what makes "flaky over the last 20 runs" cheap. Malformed or
    future-versioned lines are skipped, never fatal — a corrupt tail from a
    killed process must not make the command unusable.
    """
    path = history_path(project_root)
    if not path.is_file():
        return []
    records: list[dict] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or record.get("v") != RECORD_SCHEMA_VERSION:
                    continue
                if record.get("slug"):
                    records.append(record)
    except OSError:
        return []

    if last_runs is not None and last_runs > 0:
        seen: list[str] = []
        for record in records:
            rid = record.get("run_id")
            if rid not in seen:
                seen.append(rid)
        keep = set(seen[-last_runs:])
        records = [r for r in records if r.get("run_id") in keep]
    return records


@dataclass(frozen=True)
class FlakeStat:
    """Aggregated behaviour of one scenario over a window of runs."""

    slug: str
    runs: int
    failures: int  # runs that ended failed
    flakes: int  # runs that needed a retry to pass
    median_s: Optional[float]
    p95_s: Optional[float]
    last_outcome: str
    fingerprints: list[str]

    @property
    def flake_rate(self) -> float:
        """Fraction of runs where this scenario did not pass first try.

        Flakes and outright failures both count: from the reader's point of
        view "this scenario cost me attention" is the signal, and a scenario
        that fails outright half the time is not healthier than one that
        flakes half the time."""
        return (self.failures + self.flakes) / self.runs if self.runs else 0.0


def flake_stats(records: Iterable[dict]) -> list[FlakeStat]:
    """Aggregate records per scenario, worst first.

    Ordering is by flake rate, then by absolute count, then slug — so a
    scenario that failed 7/10 outranks one that failed 1/2 despite the lower
    ratio being computed from more evidence.
    """
    by_slug: dict[str, list[dict]] = {}
    for record in records:
        by_slug.setdefault(record["slug"], []).append(record)

    stats: list[FlakeStat] = []
    for slug, rows in by_slug.items():
        durations = [r["duration_s"] for r in rows if isinstance(r.get("duration_s"), (int, float))]
        fps = []
        for r in rows:
            fp = r.get("fingerprint")
            if fp and fp not in fps:
                fps.append(fp)
        stats.append(
            FlakeStat(
                slug=slug,
                runs=len(rows),
                failures=sum(1 for r in rows if r.get("outcome") == OUTCOME_FAILED),
                flakes=sum(1 for r in rows if r.get("outcome") == OUTCOME_FLAKY),
                median_s=round(statistics.median(durations), 1) if durations else None,
                p95_s=round(_percentile(durations, 0.95), 1) if durations else None,
                last_outcome=rows[-1].get("outcome", "unknown"),
                fingerprints=fps,
            )
        )
    stats.sort(key=lambda s: (-s.flake_rate, -(s.failures + s.flakes), s.slug))
    return stats


def _percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile. Avoids a numpy dependency for one number."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def median_durations(project_root: Path, *, last_runs: int = 10) -> dict[str, float]:
    """Median duration per scenario, for longest-first queue ordering.

    Returns ``{}`` when there is no history — the caller falls back to whatever
    order it already had.
    """
    durations: dict[str, list[float]] = {}
    for record in load(project_root, last_runs=last_runs):
        value = record.get("duration_s")
        if isinstance(value, (int, float)) and value > 0:
            durations.setdefault(record["slug"], []).append(float(value))
    return {slug: statistics.median(values) for slug, values in durations.items()}


def order_by_duration_desc(slugs: list[str], durations: dict[str, float]) -> list[str]:
    """Longest-first (LPT) ordering, preserving the caller's order for ties.

    Scenarios with no recorded duration go last: an unknown is most likely a
    newly added scenario, and guessing it is slow would push a genuinely slow
    known one behind it. Unknown-last also makes the no-history case a no-op.
    """
    if not durations:
        return list(slugs)
    known = [s for s in slugs if s in durations]
    unknown = [s for s in slugs if s not in durations]
    known.sort(key=lambda s: -durations[s])
    return known + unknown


def prune(project_root: Path, *, keep_runs: int) -> int:
    """Rewrite the history file keeping only the newest ``keep_runs`` runs.

    Returns the number of records dropped (0 on any error). Uses a temp file +
    replace so an interrupted prune can't truncate the history."""
    path = history_path(project_root)
    if not path.is_file():
        return 0
    before = load(project_root)
    kept = load(project_root, last_runs=keep_runs)
    if len(kept) == len(before):
        return 0
    tmp = path.with_suffix(".jsonl.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            for record in kept:
                f.write(json.dumps(record, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        return 0
    return len(before) - len(kept)
