"""Merge raw run records into the coverage map store.

A coverage run (``steppy run --coverage``) leaves raw, lossless observation
records under ``<records_dir>/raw/<run_id>/<scenario>/*.json`` (one per page
fixture instance) plus content-deduplicated inline source maps under
``raw/<run_id>/_maps/``. This module turns one run directory into per-scenario
contribution sets — normalizing routes, classifying + resolving network
requests against declared endpoint templates, decoding JS coverage through
source maps — and upserts them transactionally: each scenario's whole prior
contribution is replaced, so merging is idempotent and never accumulates
stale rows.

All interpretation lives here (not in the recorder) so normalization rules
can evolve without recapturing. Everything is pure given the run directory,
the store, and the settings — no network, no wall clock.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..parser import parse_scenario_file
from .normalize import (
    classify_request,
    compile_extra_patterns,
    match_template,
    normalize_path,
    normalize_route,
    route_path_from_url,
    strip_query,
)
from .sourcemap import (
    SourceMapUnusable,
    bundle_key,
    load_map_for_entry,
    modules_for_script,
)
from .store import Contribution, open_store, replace_scenario_contribution

RECORD_SCHEMA_VERSION = 1


class MergeError(RuntimeError):
    """Raised when a run directory cannot be merged."""


@dataclass
class CoverageSettings:
    """Resolved coverage configuration (paths absolute, defaults applied)."""

    store_path: Path
    records_dir: Path
    scenarios_dir: Path = Path("scenarios")
    apis: list[dict] = field(default_factory=list)  # {"name", "openapi", "base_urls"}
    sitemap: str | None = None
    sourcemap_dir: Path | None = None
    api_resource_types: tuple[str, ...] = ("xhr", "fetch")
    route_param_patterns: list[str] = field(default_factory=list)
    module_ignore: tuple[str, ...] = ("node_modules/", "webpack/")
    # Committed ``non_ui`` declarations (see coverage/triage.py). Merge never
    # reads these — they are a *read-time* overlay, so the store stays a record
    # of what was observed and declared, not of how we chose to discount it.
    non_ui: tuple[str, ...] = ()

    @classmethod
    def from_config(cls, config, project_root: Path) -> "CoverageSettings":
        """Build settings from a loaded SteppyConfig (coverage block optional)."""

        def resolve(value: str) -> Path:
            path = Path(value)
            return path if path.is_absolute() else project_root / path

        cov = getattr(config, "coverage", None)
        if cov is None:
            return cls(
                store_path=resolve("./.steppy/coverage.db"),
                records_dir=resolve("./output/coverage"),
                scenarios_dir=resolve("scenarios"),
            )
        return cls(
            store_path=resolve(cov.store_path),
            records_dir=resolve(cov.records_dir),
            scenarios_dir=resolve("scenarios"),
            apis=[{"name": a.name, "openapi": a.openapi, "base_urls": list(a.base_urls)} for a in cov.apis],
            sitemap=cov.sitemap,
            sourcemap_dir=resolve(cov.sourcemap_dir) if cov.sourcemap_dir else None,
            api_resource_types=tuple(cov.api_resource_types),
            route_param_patterns=list(cov.route_param_patterns),
            module_ignore=tuple(cov.module_ignore),
            non_ui=tuple(cov.non_ui),
        )


def latest_run_id(records_dir: Path) -> str | None:
    """Lexicographically last run dir under ``<records_dir>/raw`` (ids sort by time)."""
    raw_root = Path(records_dir) / "raw"
    if not raw_root.is_dir():
        return None
    runs = sorted(p.name for p in raw_root.iterdir() if p.is_dir() and p.name != "_maps")
    return runs[-1] if runs else None


def _load_records(run_dir: Path) -> tuple[dict[str, list[dict]], list[str]]:
    """Group record files by scenario id; malformed files become warnings."""
    by_scenario: dict[str, list[dict]] = {}
    warnings: list[str] = []
    for scenario_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        if scenario_dir.name == "_maps":
            continue
        for record_file in sorted(scenario_dir.glob("*.json")):
            try:
                record = json.loads(record_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                warnings.append(f"skipped unreadable record {record_file.name}: {exc}")
                continue
            if record.get("steppy_coverage_record") != RECORD_SCHEMA_VERSION:
                warnings.append(
                    f"skipped record {record_file.name}: unsupported schema {record.get('steppy_coverage_record')!r}"
                )
                continue
            scenario_id = record.get("scenario") or scenario_dir.name
            by_scenario.setdefault(scenario_id, []).append(record)
    return by_scenario, warnings


class _EvidenceAccumulator:
    """Merge per-record observations into one contribution per node identity."""

    def __init__(self) -> None:
        self._acc: dict[tuple[str, str, str, str], dict] = {}

    def add(
        self,
        kind: str,
        key: str,
        *,
        api: str = "",
        method: str = "",
        sample: str | None = None,
        count: int = 1,
        record: dict,
    ) -> None:
        ident = (kind, api, method, key)
        entry = self._acc.setdefault(
            ident,
            {
                "samples": set(),
                "count": 0,
                "roles": set(),
                "guide_mode": False,
                "outcomes": set(),
                "asserts_enabled": False,
            },
        )
        if sample:
            entry["samples"].add(sample)
        entry["count"] += count
        if record.get("role"):
            entry["roles"].add(record["role"])
        entry["guide_mode"] = entry["guide_mode"] or bool(record.get("guide_mode"))
        entry["outcomes"].add(record.get("outcome") or "unknown")
        entry["asserts_enabled"] = entry["asserts_enabled"] or bool(record.get("asserts_enabled"))

    def contributions(self) -> list[Contribution]:
        result = []
        for (kind, api, method, key), entry in sorted(self._acc.items()):
            if entry["outcomes"] & {"failed"}:
                outcome = "failed"
            elif entry["outcomes"] == {"passed"}:
                outcome = "passed"
            else:
                outcome = "unknown"
            evidence = {
                "samples": sorted(entry["samples"])[:5],
                "count": entry["count"],
                "roles": sorted(entry["roles"]),
                "guide_mode": entry["guide_mode"],
                "outcome": outcome,
            }
            result.append(
                Contribution(
                    kind=kind,
                    key=key,
                    api=api,
                    method=method,
                    evidence=evidence,
                    asserts_enabled=entry["asserts_enabled"],
                )
            )
        return result


def _route_events(record: dict) -> list[dict]:
    """Merge nav + SPA streams by timestamp; collapse consecutive same-URL events."""
    events = [
        e
        for e in list(record.get("nav_events") or []) + list(record.get("spa_events") or [])
        if isinstance(e, dict) and str(e.get("u", "")).startswith(("http://", "https://"))
    ]
    events.sort(key=lambda e: (e.get("t") or 0, e.get("u", ""), e.get("k", "")))
    collapsed: list[dict] = []
    for event in events:
        if collapsed and collapsed[-1]["u"] == event["u"]:
            continue
        collapsed.append(event)
    return collapsed


def build_contributions(
    records: list[dict],
    settings: CoverageSettings,
    declared_templates: dict[tuple[str, str], list[str]],
    maps_dir: Path | None,
) -> tuple[list[Contribution], dict]:
    """Union one scenario's records into its contribution set.

    ``declared_templates`` maps ``(api, method)`` to known OpenAPI path
    templates: observed requests matching a template attach to the declared
    node identity; others get a heuristic-normalized observed identity (the
    refresh re-link pass can move them later).
    """
    extra = compile_extra_patterns(settings.route_param_patterns)
    acc = _EvidenceAccumulator()
    info = {"records": len(records), "modules_unavailable": False}

    for record in sorted(
        records,
        key=lambda r: (r.get("role") or "", r.get("locale") or "", r.get("layout") or "", r.get("noauth") or False),
    ):
        # Routes
        for event in _route_events(record):
            url = event["u"]
            acc.add(
                "route",
                normalize_route(url, extra),
                sample=strip_query(route_path_from_url(url)),
                record=record,
            )

        # Endpoints
        for request in record.get("requests") or []:
            classified = classify_request(
                request.get("m", ""),
                request.get("u", ""),
                request.get("rt", ""),
                settings.apis,
                settings.api_resource_types,
            )
            if classified is None:
                continue
            api, method, path = classified["api"], classified["method"], classified["path"]
            template = match_template(path, declared_templates.get((api, method), []))
            key = template if template else normalize_path(strip_query(path), extra)
            acc.add(
                "endpoint",
                key,
                api=api,
                method=method,
                sample=strip_query(path),
                record=record,
            )

        # Modules (JS coverage)
        js = record.get("js_coverage") or {}
        if not js.get("ok"):
            info["modules_unavailable"] = True
            continue
        for entry in js.get("scripts") or []:
            offsets = entry.get("executed_offsets") or []
            if not offsets:
                continue
            line_starts = entry.get("line_starts")
            map_obj = load_map_for_entry(entry, maps_dir, settings.sourcemap_dir)
            modules: set[str]
            if map_obj is None or not line_starts:
                modules = {bundle_key(entry.get("url", ""))}
            else:
                try:
                    modules = modules_for_script(offsets, line_starts, map_obj, settings.module_ignore)
                except SourceMapUnusable:
                    modules = {bundle_key(entry.get("url", ""))}
            for module in sorted(modules):
                acc.add("module", module, record=record)

    return acc.contributions(), info


def count_scenario_asserts(scenarios_dir: Path, scenario_id: str) -> int:
    """Assert steps in the scenario markdown (0 when missing/unparseable).

    Counts both ``action: assert`` steps and other actions carrying an
    ``expect:`` — the same set the generated test wraps in ``expect()``.
    """
    path = Path(scenarios_dir) / f"{scenario_id}.md"
    if not path.is_file():
        return 0
    try:
        scenario = parse_scenario_file(path)
    except Exception:
        return 0
    return sum(1 for step in scenario.steps if step.action == "assert" or getattr(step, "expect", None))


def _declared_endpoint_templates(conn) -> dict[tuple[str, str], list[str]]:
    templates: dict[tuple[str, str], list[str]] = {}
    for row in conn.execute(
        "SELECT DISTINCT n.api, n.method, n.key FROM nodes n"
        " JOIN declared d ON d.node_id = n.id"
        " WHERE n.kind = 'endpoint' ORDER BY n.api, n.method, n.key"
    ):
        templates.setdefault((row["api"], row["method"]), []).append(row["key"])
    return templates


def merge_run(settings: CoverageSettings, run_id: str | None = None) -> dict:
    """Merge one recorded run into the store. Returns a summary dict.

    ``run_id=None`` merges the latest run under ``<records_dir>/raw``.
    """
    if run_id is None:
        run_id = latest_run_id(settings.records_dir)
        if run_id is None:
            raise MergeError(
                f"no coverage runs found under {Path(settings.records_dir) / 'raw'} — "
                "record one with `steppy run --coverage`"
            )
    run_dir = Path(settings.records_dir) / "raw" / run_id
    if not run_dir.is_dir():
        raise MergeError(f"coverage run directory not found: {run_dir}")

    by_scenario, warnings = _load_records(run_dir)
    if not by_scenario:
        raise MergeError(f"no coverage records in {run_dir}")

    maps_dir = run_dir / "_maps"
    conn = open_store(settings.store_path)
    try:
        declared_templates = _declared_endpoint_templates(conn)
        summary: dict = {"run_id": run_id, "scenarios": {}, "warnings": warnings}
        for scenario_id in sorted(by_scenario):
            records = by_scenario[scenario_id]
            contributions, info = build_contributions(
                records, settings, declared_templates, maps_dir if maps_dir.is_dir() else None
            )
            recorded_at = max((str(r.get("finished_at") or "") for r in records), default="")
            replace_scenario_contribution(
                conn,
                scenario_id,
                contributions,
                asserts_count=count_scenario_asserts(settings.scenarios_dir, scenario_id),
                run_id=run_id,
                recorded_at=recorded_at,
            )
            summary["scenarios"][scenario_id] = {"nodes": len(contributions), **info}
        summary["totals"] = {
            "scenarios": len(summary["scenarios"]),
            "nodes": sum(s["nodes"] for s in summary["scenarios"].values()),
        }
        return summary
    finally:
        conn.close()
