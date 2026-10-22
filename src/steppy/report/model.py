"""Bundle manifest + event timeline + coverage → :class:`ReportModel`.

**This module must never import jinja2.** It is the half of the reporter a control
plane can consume — ``from steppy.report.model import build_model`` — and dragging
a renderer along would defeat the point of the model/render split. That is the
concrete form of "one renderer, not two": the *model* is shared, the presentation
is not. (It also may not import :mod:`steppy.guide`, which builds a module-level
``MarkdownIt``; a guard test forbids ``markdown_it`` on the default import path.)

The build is a pure function of its inputs, with no clock and no filesystem reads.
Everything it needs has already been fetched: the manifest carries the file
inventory *with* attribution (``bundle/classify.py`` did that work at create
time, so nothing here parses a path), the timeline comes from the event JSONL, and
coverage arrives resolved. Same input, same bytes out — which is what lets a test
assert on the model and CI diff two reports.
"""

from __future__ import annotations

import base64
from typing import Iterable, Optional

from ..contracts.bundle import BundleFile, BundleManifest, BundleTest
from ..contracts.report import (
    REPORT_SCHEMA_VERSION,
    ReportArtifactGroup,
    ReportArtifacts,
    ReportCapabilities,
    ReportCoverage,
    ReportFailureCause,
    ReportFailureOccurrence,
    ReportModel,
    ReportScenario,
    ReportSourceInfo,
    ReportStep,
    ReportTotals,
    ReportVariant,
)

#: Worst-first. A run's verdict, and a scenario's, is the worst thing that
#: happened to it — a flow that fails only in Dutch on mobile is a failing flow.
#: Unknown outcomes sort *after* ``failed`` and before ``passed``: something we
#: cannot classify is not a pass, and claiming it is a failure would be a guess.
_OUTCOME_RANK = {
    "worker_died": 0,
    "error": 1,
    "failed": 2,
    "interrupted": 3,
    "unknown": 4,
    "skipped": 5,
    "passed": 6,
}
_UNRANKED = 4

#: Outcomes that do not fail a gate. ``skipped`` is deliberate scope, and the
#: coordinator already treats a pass-on-retry as non-fatal.
_PASSING = frozenset({"passed", "skipped"})


def worse(a: str, b: str) -> str:
    """The worse of two outcomes, by :data:`_OUTCOME_RANK`."""
    return a if _rank(a) <= _rank(b) else b


def _rank(outcome: str) -> int:
    return _OUTCOME_RANK.get(outcome, _UNRANKED)


def _decode_tail(test: BundleTest) -> str:
    if not test.fail_tail_b64:
        return ""
    try:
        return base64.b64decode(test.fail_tail_b64).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        # A corrupt tail is not worth losing the row over — the outcome, the step
        # and the screenshot all still say something useful.
        return ""


class _Row:
    """Duck-type for ``_parallel.fingerprint.group_failures``.

    That function needs only ``.slug`` and ``.fail_tails``, which is why the
    parallel renderer's grouping works here unchanged rather than being
    reimplemented — the console summary and the HTML report collapse identical
    failures identically, by construction.
    """

    __slots__ = ("slug", "fail_tails")

    def __init__(self, slug: str, fail_tails: list[tuple[str, str]]):
        self.slug = slug
        self.fail_tails = fail_tails


def group_manifest_failures(tests: Iterable[BundleTest]) -> tuple[list[ReportFailureCause], dict[str, str]]:
    """``(causes, {nodeid: fingerprint})`` for every failing test.

    Returns the per-nodeid map as well so each variant can link to its cause
    without the templates re-deriving anything.
    """
    from .._parallel.fingerprint import fingerprint, group_failures

    by_slug: dict[str, list[tuple[str, str]]] = {}
    context: dict[str, BundleTest] = {}
    prints: dict[str, str] = {}
    for test in tests:
        if test.outcome in _PASSING:
            continue
        tail = _decode_tail(test)
        by_slug.setdefault(test.scenario, []).append((test.nodeid, tail))
        context[test.nodeid] = test
        prints[test.nodeid] = fingerprint(tail)

    rows = [_Row(slug, tails) for slug, tails in by_slug.items()]
    causes = []
    for group in group_failures(rows):
        occurrences = []
        exemplar = ""
        for _slug, nodeid, tail in group.occurrences:
            test = context.get(nodeid)
            occurrences.append(
                ReportFailureOccurrence(
                    scenario=test.scenario if test else _slug,
                    nodeid=nodeid,
                    locale=test.locale if test else None,
                    layout=test.layout if test else None,
                )
            )
            if not exemplar:
                exemplar = tail
        causes.append(
            ReportFailureCause(
                fingerprint=group.fingerprint,
                headline=group.headline,
                count=group.count,
                scenarios=group.slugs,
                occurrences=occurrences,
                exemplar_tail=exemplar,
            )
        )
    return causes, prints


def scenario_href(slug: str) -> str:
    """Site-relative URL of a scenario page.

    One definition, used for the link *and* for the filename the renderer writes —
    two would drift, and they would drift silently into a 404. The slug is
    sanitised for the same reason asset paths are: it may have arrived in a
    downloaded manifest, and here it decides a path on disk.
    """
    from .assets import sanitise

    return f"scenarios/{sanitise(slug)}.html"


def build_model(
    *,
    manifest: BundleManifest,
    source: ReportSourceInfo,
    attempts: Optional[dict] = None,
    coverage: Optional[ReportCoverage] = None,
    assets: Optional[dict] = None,
    screenshots_policy: str = "none",
    steppy_version: str = "",
    extra_warnings: Optional[list[str]] = None,
) -> ReportModel:
    """Assemble the report model.

    ``attempts`` is ``{nodeid: [TestAttempt]}`` from
    ``bundle.results.steps_from_records``; ``assets`` maps a manifest path to the
    site-relative URL it was copied to (absent ⇒ not copied, which is a policy
    outcome rather than an error).
    """
    attempts = attempts or {}
    assets = assets or {}
    coverage = coverage or ReportCoverage()

    tests = list(manifest.tests)
    causes, prints = group_manifest_failures(tests)

    # Scenarios come from the tests AND the files, so a run made without
    # --bundle — no test rows at all — still lists what it captured instead of
    # rendering an empty page.
    slugs: list[str] = []
    seen: set[str] = set()
    for name in [t.scenario for t in tests] + [f.scenario for f in manifest.files if f.scenario]:
        if name and name not in seen:
            seen.add(name)
            slugs.append(name)
    slugs.sort()

    by_scenario: dict[str, list[BundleTest]] = {}
    for test in tests:
        by_scenario.setdefault(test.scenario, []).append(test)

    failure_shots = _index_failure_screenshots(manifest, assets)
    failure_logs = _index_by_scenario(manifest, "failure_log")
    traces = _index_paths(manifest, "trace")
    videos = _index_paths(manifest, "video")

    scenarios: list[ReportScenario] = []
    any_steps = False
    any_attempts = False
    outcomes: dict[str, int] = {}
    duration_sum = 0
    verdict = "passed"

    for slug in slugs:
        rows = by_scenario.get(slug, [])
        variants: list[ReportVariant] = []
        scenario_outcome = "passed" if rows else "unknown"
        scenario_counts: dict[str, int] = {}
        scenario_duration = 0

        for test in sorted(rows, key=lambda t: (t.locale or "", t.layout or "", t.nodeid)):
            runs = attempts.get(test.nodeid) or []
            if len(runs) > 1:
                any_attempts = True
            last = runs[-1] if runs else None
            steps = _steps_for(last, test, assets, manifest)
            if steps:
                any_steps = True

            variants.append(
                ReportVariant(
                    nodeid=test.nodeid,
                    locale=test.locale,
                    layout=test.layout,
                    outcome=test.outcome,
                    duration_ms=test.duration_ms,
                    attempts=max(len(runs), 1),
                    attempt_outcomes=[a.outcome or "unknown" for a in runs],
                    failed_step=test.failed_step,
                    failed_step_title=_step_title(steps, test.failed_step),
                    fingerprint=prints.get(test.nodeid),
                    fail_tail=_decode_tail(test),
                    steps=steps,
                    failure_screenshot=failure_shots.get(test.nodeid),
                    trace_path=traces.get(test.nodeid),
                    video_path=videos.get(test.nodeid),
                )
            )
            outcomes[test.outcome] = outcomes.get(test.outcome, 0) + 1
            scenario_counts[test.outcome] = scenario_counts.get(test.outcome, 0) + 1
            scenario_outcome = worse(scenario_outcome, test.outcome)
            verdict = worse(verdict, test.outcome)
            duration_sum += test.duration_ms or 0
            scenario_duration += test.duration_ms or 0

        scenarios.append(
            ReportScenario(
                slug=slug,
                outcome=scenario_outcome,
                outcomes=scenario_counts,
                duration_ms_sum=scenario_duration,
                href=scenario_href(slug),
                variants=variants,
                failure_log=failure_logs.get(slug),
            )
        )

    not_passed = sum(1 for s in scenarios if s.outcome not in _PASSING)
    if not tests:
        # No outcomes were recorded at all — a run without ``--bundle``, or a
        # capture-only guide run. "passed" would be a claim we have no evidence
        # for, which is the exact failure mode this whole model tries to avoid.
        # ``exit_code`` stays 0 because nothing *failed*: the run itself already
        # reported its own verdict, and a report is not the gate.
        verdict = "unknown"
        exit_code = 0
    else:
        exit_code = 0 if verdict in _PASSING else 1
    totals = ReportTotals(
        tests=len(tests),
        scenarios=len(scenarios),
        scenarios_not_passed=not_passed,
        outcomes=dict(sorted(outcomes.items())),
        duration_ms_sum=duration_sum,
        verdict=verdict,
        exit_code=exit_code,
    )

    warnings = list(manifest.warnings) + list(extra_warnings or [])
    return ReportModel(
        steppy_report=REPORT_SCHEMA_VERSION,
        generated_by=steppy_version,
        source=source,
        run=manifest.run,
        totals=totals,
        dimensions=_observed_dimensions(tests),
        capabilities=ReportCapabilities(
            step_timeline=any_steps,
            attempts=any_attempts,
            coverage=coverage.tier,
            screenshots=screenshots_policy,
            strength_reliable=coverage.tier != "records",
        ),
        scenarios=scenarios,
        failure_causes=causes,
        coverage=coverage,
        artifacts=_artifacts(manifest, assets),
        warnings=warnings,
        truncated=manifest.truncated,
    )


def _observed_dimensions(tests: list[BundleTest]) -> dict[str, list[str]]:
    """Locales and layouts that actually produced a row.

    Not ``run.spec``: a scenario whose frontmatter ``layouts:`` is disjoint from
    the run's is silently deselected, so a matrix built from the *requested* set
    grows a column of empty cells that reads like a failure.
    """
    locales = sorted({t.locale for t in tests if t.locale})
    layouts = sorted({t.layout for t in tests if t.layout})
    return {"locales": locales, "layouts": layouts}


def _steps_for(attempt, test: BundleTest, assets: dict, manifest: BundleManifest) -> list[ReportStep]:
    """The timeline for one variant, with each step's captures attached."""
    if attempt is None:
        return []
    shots = _index_step_screenshots(manifest, assets, test.scenario, test.locale, test.layout)
    steps = []
    for row in attempt.offsets():
        steps.append(ReportStep(**row, screenshots=shots.get(row["step"], [])))
    return steps


def _step_title(steps: list[ReportStep], failed_step: Optional[int]) -> str:
    if failed_step is None:
        return ""
    for step in steps:
        if step.step == failed_step:
            return step.title
    return ""


def _index_step_screenshots(
    manifest: BundleManifest, assets: dict, scenario: Optional[str], locale, layout
) -> dict[int, list[str]]:
    """``{step: [url, …]}`` for one variant, in frame order.

    Reads ``manifest.files[]`` attribution rather than parsing paths — that is what
    ``bundle/classify.py`` recorded it for.
    """
    out: dict[int, list[tuple[str, str]]] = {}
    for entry in manifest.files:
        if entry.kind != "screenshot" or entry.step is None:
            continue
        if entry.scenario != scenario or entry.locale != locale or entry.layout != layout:
            continue
        url = assets.get(entry.path)
        if url:
            out.setdefault(entry.step, []).append((entry.frame or "", url))
    return {step: [url for _frame, url in sorted(frames)] for step, frames in out.items()}


def _index_failure_screenshots(manifest: BundleManifest, assets: dict) -> dict[str, str]:
    """``{nodeid: url}``, joined through the manifest's own attribution.

    ``sanitise_nodeid`` is lossy and must only ever be used forwards, so nothing
    here tries to reverse a ``_failures/`` filename: ``classify`` already resolved
    each one to a (scenario, locale, layout) at create time.
    """
    by_key: dict[tuple, str] = {}
    for entry in manifest.files:
        if entry.kind != "failure_screenshot":
            continue
        url = assets.get(entry.path)
        if url:
            by_key.setdefault((entry.scenario, entry.locale, entry.layout), url)
    return {
        test.nodeid: url
        for test in manifest.tests
        if (url := by_key.get((test.scenario, test.locale, test.layout))) is not None
    }


def _index_by_scenario(manifest: BundleManifest, kind: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for entry in manifest.files:
        if entry.kind == kind and entry.scenario:
            out.setdefault(entry.scenario, entry.path)
    return out


def _index_paths(manifest: BundleManifest, kind: str) -> dict[str, str]:
    """``{nodeid: in-bundle path}`` for artifacts that are named, not copied."""
    by_key: dict[tuple, str] = {}
    for entry in manifest.files:
        if entry.kind == kind:
            by_key.setdefault((entry.scenario, entry.locale, entry.layout), entry.path)
    return {
        test.nodeid: path
        for test in manifest.tests
        if (path := by_key.get((test.scenario, test.locale, test.layout))) is not None
    }


def _artifacts(manifest: BundleManifest, assets: dict) -> ReportArtifacts:
    by_kind: dict[str, ReportArtifactGroup] = {}
    total_bytes = 0
    for entry in manifest.files:
        group = by_kind.setdefault(entry.kind, ReportArtifactGroup())
        group.count += 1
        group.bytes += entry.size
        total_bytes += entry.size
    copied = {url for url in assets.values() if url}
    return ReportArtifacts(
        files=len(manifest.files),
        bytes=total_bytes,
        deduped=max(len(assets) - len(copied), 0),
        by_kind=dict(sorted(by_kind.items())),
    )


def files_of_kind(manifest: BundleManifest, *kinds: str) -> list[BundleFile]:
    """Manifest rows of any of *kinds*, in manifest order (sorted by path)."""
    wanted = set(kinds)
    return [f for f in manifest.files if f.kind in wanted]
