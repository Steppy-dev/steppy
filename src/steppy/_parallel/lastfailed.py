"""Write pytest's ``lastfailed`` cache from a parallel run's results so
``steppy rerun`` re-runs the scenarios that failed in *that* run.

``steppy rerun`` reads ``<cwd>/.pytest_cache/v/cache/lastfailed`` and feeds
it to ``pytest --lf``. Only the single-worker ``steppy run`` path populates
that cache (plain pytest, cache provider active). A parallel
``steppy run --workers N`` never touches it — workers run with
``-p no:cacheprovider`` and the project root is bind-mounted read-only into
each container — so after a parallel run the cache still reflects the last
*single-worker* run (or is absent), and ``rerun`` re-runs an OLD failure set.

This module closes that gap: the coordinator calls
:func:`write_lastfailed_cache` after it aggregates results, writing the same
cache file ``rerun`` reads. Both run modes then converge on one file, so
"last run wins" becomes automatic and ``rerun`` needs no changes.

**Granularity is file-level** — one key per failed *scenario file* (a key
with no ``::``). ``pytest --lf`` treats a bare file path as "re-run the whole
file": collection is restricted to the failed files via the plugin's
``_last_failed_paths`` and, with no per-node key matching, nothing is
deselected, so every test in each failed file runs. This is robust — it
covers orphaned/crashed workers and the "events plugin didn't load"
degenerate case, neither of which yields node IDs — and it never drops a
failed scenario. (Mixing file-level and node-level keys is unsafe: a matching
node-level key makes pytest deselect everything else, including file-level
entries, so we keep the keys uniformly file-level.)

Lazy-imported from :mod:`steppy.coordinator` so it only loads in parallel
mode (the single-worker ``test_no_parallel_import_on_default_path`` guard
relies on this).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .event_bus import EventBus
from .queue import ScenarioResult


def write_lastfailed_cache(
    *,
    project_root: Path,
    tests_dir: Path,
    scenario_results: list[ScenarioResult],
    orphans: dict[str, list[str]],
    bus: EventBus,
    failed_slugs: Optional[set[str]] = None,
) -> Optional[Path]:
    """Write ``project_root/.pytest_cache/v/cache/lastfailed`` from a parallel
    run's outcome.

    Returns the path written, or ``None`` if writing failed. The function
    NEVER raises — cache writing is best-effort; any ``OSError`` is swallowed
    so a filesystem hiccup on teardown can't crash the coordinator.

    The cache is written **unconditionally** — an all-pass run writes an empty
    dict so a subsequent ``rerun`` correctly reports "all passed" instead of
    re-running a stale older failure set. That reset is the crux of the fix.

    ``failed_slugs`` lets the caller supply the authoritative failure set. The
    retry pass uses it to pass only the scenarios that failed *every* attempt —
    a flaky scenario ended green, so putting it here would make ``steppy rerun``
    re-run known-good tests. Omitted ⇒ derived from ``scenario_results`` +
    ``orphans`` exactly as before.
    """
    if failed_slugs is None:
        # Failed scenarios: authoritative per-scenario exit codes (mirrors
        # coordinator's `failed = sum(...)`) plus orphans (claimed, never reported).
        failed_slugs = {r.slug for r in scenario_results if r.exit_code != 0}
        for slugs in orphans.values():
            failed_slugs.update(slugs)

    # Map slug -> real test-file path exactly as pytest spelled it inside the
    # worker (rootdir-relative). The container mounts the same project files at
    # /steppy/project, so the worker's rootdir-relative nodeid matches what the
    # host `pytest --lf` will compute. Prefer this over guessing the rootdir.
    file_part_by_slug: dict[str, str] = {}
    for t in bus.snapshot().tests:
        if not t.scenario_slug:
            continue
        file_part = t.nodeid.split("::", 1)[0]
        # Skip synthetic nodeids (e.g. "<synthetic>::slug") and anything that
        # isn't a real test file.
        if file_part.endswith(".py"):
            file_part_by_slug.setdefault(t.scenario_slug, file_part)

    tests_rel = _tests_rel_posix(tests_dir, project_root)

    lastfailed: dict[str, bool] = {}
    for slug in sorted(failed_slugs):
        key = file_part_by_slug.get(slug) or f"{tests_rel}/test_{slug}.py"
        lastfailed[key] = True

    cache_dir = project_root / ".pytest_cache" / "v" / "cache"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "lastfailed").write_text(json.dumps(lastfailed, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        return None

    # Keep the cache out of git, exactly as pytest does when it owns the dir.
    gitignore = project_root / ".pytest_cache" / ".gitignore"
    if not gitignore.exists():
        try:
            gitignore.write_text("# Created by steppy automatically.\n*\n", encoding="utf-8")
        except OSError:
            pass

    return cache_dir / "lastfailed"


def _tests_rel_posix(tests_dir: Path, project_root: Path) -> str:
    """The tests dir as a posix path relative to the project root.

    pytest node IDs are always posix (``/``) even on Windows, so the file-level
    fallback key must be too. Falls back to ``tests_dir`` as-is when it isn't
    under ``project_root``.
    """
    try:
        return tests_dir.resolve().relative_to(project_root.resolve()).as_posix()
    except (ValueError, OSError):
        return tests_dir.as_posix()
