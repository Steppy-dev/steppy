"""Where the report's coverage numbers come from, in order of trustworthiness.

Named ``coverage_source`` rather than ``coverage`` so ``from . import coverage``
inside this package cannot shadow :mod:`steppy.coverage`.

The problem this module exists to solve. The coverage map is a sqlite store at
``.steppy/coverage.db``, which lives **outside** ``output/`` on purpose — cleaning
run artifacts must never wipe hand-made triage flags — and is therefore never
inside a results bundle. The *declared* surface it is scored against comes from
``coverage refresh``, which fetches an OpenAPI document over the network. So a
bundle downloaded from someone else's CI carries the raw per-scenario records and
nothing that says what they should be compared to.

Four tiers, best first:

===========  ==========================================  ===============================
tier         source                                      when
===========  ==========================================  ===============================
``bundled``  ``output/coverage/gaps.json`` in the run    any input that carries one
``store``    the project's live ``.steppy/coverage.db``  **live-tree input only**
``records``  a throwaway store rebuilt from raw records  records, no gaps.json, no store
``none``     no coverage section at all                  nothing was recorded
===========  ==========================================  ===============================

Two of those rows are guardrails against a plausible-looking lie, and both are the
kind of bug that would never surface as a crash:

**``store`` is live-tree only.** Reading this machine's map to describe a bundle
that came from elsewhere would attribute *our* coverage to *their* run and print a
confident percentage about it.

**``records`` cannot report assert strength.** Rebuilding via ``merge_run`` calls
``count_scenario_asserts``, which reads ``scenarios/*.md`` — and a bundle carries
none, so it returns 0 for every scenario and ``gaps._strength`` marks every tested
endpoint "weak". So that tier suppresses strength outright and says why, rather
than publishing a number that looks like a finding.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Sequence

from ..contracts.report import ReportBadge, ReportCoverage, ReportNonUi

TIER_BUNDLED = "bundled"
TIER_STORE = "store"
TIER_RECORDS = "records"
TIER_NONE = "none"

#: Manifest kind of the gap report that travels with a run (``bundle/classify``).
GAPS_KIND = "coverage_gaps"
RECORD_KIND = "coverage_record"

STRENGTH_UNKNOWN_WARNING = (
    "assert strength is unavailable: this coverage was rebuilt from raw records, and a bundle "
    "carries no scenarios/*.md to count assert steps in — every tested endpoint would otherwise "
    "read as weakly covered"
)


def resolve_coverage(
    source,
    *,
    project_root: Path,
    non_ui: Sequence[str] = (),
    surface: str = "all",
    badge_label: Optional[str] = None,
    href: Optional[str] = None,
) -> ReportCoverage:
    """Best available coverage for *source*. Never raises — degrades and says so."""
    from ..coverage.report import DEFAULT_BADGE_LABEL

    label = badge_label or DEFAULT_BADGE_LABEL
    warnings: list[str] = []

    gaps, tier = _from_bundle(source, warnings)
    if gaps is None and source.is_live_tree:
        gaps, tier = _from_store(project_root, non_ui, warnings)
    if gaps is None:
        gaps, tier = _from_records(source, project_root, non_ui, warnings)
    if gaps is None:
        return ReportCoverage(tier=TIER_NONE, warnings=warnings)

    if tier == TIER_RECORDS:
        warnings.append(STRENGTH_UNKNOWN_WARNING)

    return _view(gaps, tier=tier, surface=surface, label=label, href=href, warnings=warnings)


def _view(
    gaps: dict,
    *,
    tier: str,
    surface: str,
    label: str,
    href: Optional[str],
    warnings: list[str],
) -> ReportCoverage:
    from ..coverage.report import badge_payload

    ratio = (gaps.get("e2e_surface") or {}).get(surface) or {}
    payload = badge_payload(gaps, surface=surface, label=label)
    return ReportCoverage(
        tier=tier,
        counts=gaps.get("counts") or {},
        e2e_surface=gaps.get("e2e_surface") or {},
        buckets={kind: gaps[kind] for kind in ("endpoints", "routes", "modules") if isinstance(gaps.get(kind), dict)},
        non_ui=_non_ui_summary(gaps),
        badge=ReportBadge(
            surface=surface,
            label=label,
            tested=ratio.get("tested", 0),
            declared=ratio.get("declared", 0),
            ratio=ratio.get("ratio"),
            percent=ratio.get("percent"),
            color=payload["color"],
        ),
        href=href,
        warnings=warnings,
    )


def _non_ui_summary(gaps: dict) -> ReportNonUi:
    """How much was discounted, and on whose authority.

    ``by_source`` is what lets a reader see a percentage was raised by committed
    config rather than by a flag somebody set locally and never shared.
    """
    counts = gaps.get("counts") or {}
    declared = sum((counts.get(kind) or {}).get("declared_non_ui", 0) for kind in ("endpoints", "routes"))
    tested = sum((counts.get(kind) or {}).get("tested_non_ui", 0) for kind in ("endpoints", "routes"))
    by_source: dict[str, int] = {}
    for kind in ("endpoints", "routes"):
        section = gaps.get(kind) or {}
        for bucket in ("tested", "declared_unreached"):
            for entry in section.get(bucket) or []:
                if entry.get("triage") == "non_ui":
                    key = entry.get("triage_source") or "unspecified"
                    by_source[key] = by_source.get(key, 0) + 1
    return ReportNonUi(declared=declared, tested=tested, by_source=dict(sorted(by_source.items())))


def _from_bundle(source, warnings: list[str]) -> tuple[Optional[dict], str]:
    """The gap report that travelled with the run, if it did."""
    entries = [f for f in source.manifest.files if f.kind == GAPS_KIND]
    if not entries:
        return None, TIER_NONE
    try:
        fetched, fetch_warnings = source.fetch(texts={entries[0].path})
    except Exception as e:
        warnings.append(f"coverage: could not read {entries[0].path} from the bundle ({e})")
        return None, TIER_NONE
    warnings.extend(fetch_warnings)
    payload = fetched.get(entries[0].path)
    if not payload:
        return None, TIER_NONE
    try:
        gaps = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        warnings.append(f"coverage: {entries[0].path} is not readable JSON ({e})")
        return None, TIER_NONE
    if not isinstance(gaps, dict):
        return None, TIER_NONE
    return gaps, TIER_BUNDLED


def _from_store(project_root: Path, non_ui: Sequence[str], warnings: list[str]) -> tuple[Optional[dict], str]:
    """The project's own map. Only ever reached for a live ``output/`` tree."""
    try:
        from ..config import load_config
        from ..coverage.gaps import compute_gaps
        from ..coverage.merge import CoverageSettings
        from ..coverage.store import open_store, read_map
        from ..coverage.triage import apply_config_triage

        settings = CoverageSettings.from_config(load_config(project_root / "config.yaml"), project_root)
        store_path = Path(settings.store_path)
        if not store_path.is_file():
            return None, TIER_NONE
        patterns = tuple(non_ui) or settings.non_ui
        conn = open_store(store_path)
        try:
            gaps = compute_gaps(apply_config_triage(read_map(conn), patterns))
        finally:
            conn.close()
        return gaps, TIER_STORE
    except Exception as e:
        warnings.append(f"coverage: the local map at .steppy/coverage.db could not be read ({e})")
        return None, TIER_NONE


def _from_records(source, project_root: Path, non_ui: Sequence[str], warnings: list[str]) -> tuple[Optional[dict], str]:
    """Last resort: rebuild a throwaway map from the raw records in the bundle.

    Observed nodes only. There is no declared surface here (that needs the network)
    and no triage table, so the buckets are honest about what was *exercised* and
    silent about what was missed.
    """
    entries = [f for f in source.manifest.files if f.kind == RECORD_KIND]
    if not entries:
        return None, TIER_NONE

    import tempfile

    try:
        from ..config import ConfigError, load_config
        from ..coverage.gaps import compute_gaps
        from ..coverage.merge import CoverageSettings, merge_run
        from ..coverage.store import open_store, read_map
        from ..coverage.triage import apply_config_triage
    except Exception as e:  # pragma: no cover - import failure is not a report failure
        warnings.append(f"coverage: records present but the coverage modules could not be loaded ({e})")
        return None, TIER_NONE

    run_id = source.manifest.run.run_id
    with tempfile.TemporaryDirectory(prefix="steppy-report-cov-") as tmp:
        tmp_root = Path(tmp)
        records_dir = tmp_root / "coverage"
        wanted = {entry.path: records_dir / _record_rel(entry.path) for entry in entries}
        try:
            _, fetch_warnings = source.fetch(into=wanted)
        except Exception as e:
            warnings.append(f"coverage: raw records could not be read ({e})")
            return None, TIER_NONE
        warnings.extend(fetch_warnings)

        try:
            base = load_config(project_root / "config.yaml")
        except (ConfigError, OSError):
            base = None
        settings = (
            CoverageSettings.from_config(base, project_root)
            if base is not None
            else CoverageSettings(store_path=tmp_root / "map.db", records_dir=records_dir)
        )
        settings = _relocated(settings, store_path=tmp_root / "map.db", records_dir=records_dir)

        try:
            merge_run(settings, run_id)
            conn = open_store(settings.store_path)
            try:
                patterns = tuple(non_ui) or settings.non_ui
                gaps = compute_gaps(apply_config_triage(read_map(conn), patterns))
            finally:
                conn.close()
        except Exception as e:
            warnings.append(f"coverage: raw records could not be merged ({e})")
            return None, TIER_NONE
    return gaps, TIER_RECORDS


def _relocated(settings, *, store_path: Path, records_dir: Path):
    """A copy of *settings* pointed at a throwaway store.

    ``dataclasses.replace`` rather than mutation: the caller's settings describe
    the project, and quietly repointing them at a temp directory is the sort of
    thing that works until something else reads them.
    """
    from dataclasses import replace

    return replace(settings, store_path=store_path, records_dir=records_dir)


def _record_rel(manifest_path: str) -> Path:
    """``output/coverage/raw/<run>/<scenario>/<file>`` → ``raw/<run>/<scenario>/<file>``.

    ``merge_run`` expects the ``raw/<run_id>/`` layout under its ``records_dir``,
    so the tail from ``raw/`` onwards is what has to be reproduced. Falls back to
    the basename if the path is not that shape, which keeps a malformed manifest
    from escaping the temp directory.
    """
    parts = Path(manifest_path).parts
    if "raw" in parts:
        return Path(*parts[parts.index("raw") :])
    return Path(Path(manifest_path).name)
