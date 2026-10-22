"""Path → ``BundleFile`` classification.

Pure except for the ``stat``/hash the caller performs: given a path relative to
the project root and a little context (which run, which locales and layouts the
project declares, which nodeids actually ran), decide what a file *is* and what
scenario / locale / layout / step it belongs to.

Two shapes in the output tree resist naive parsing, and both are handled here
rather than guessed at by the caller:

**A one-segment capture directory is ambiguous.** ``<slug>/fr/`` and
``<slug>/desktop/`` are structurally identical, so only the name distinguishes
them — resolved against the configured sets by :mod:`steppy.captures`.

**Failure screenshots, traces and videos are keyed by a sanitised nodeid**,
and the sanitiser is lossy (``::``→``__``, ``[``→``(``): a slug containing an
underscore is indistinguishable from a separator. So we never invert it. We
build the *forward* map instead — sanitise every nodeid the run reported and
look the filename up — which is exact for every test that ran, and honestly
unattributed for anything else.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from ..captures import FAILURES_DIRNAME, classify_capture_path, sanitise_nodeid
from ..contracts.bundle import BundleFile

# ── kinds ───────────────────────────────────────────────────────────────────
# A consumer stores unknown kinds opaquely (contract rule 2), so adding one is
# always safe. `other` is the explicit bucket for anything unrecognised — a
# bundle that silently DROPS a file is far worse than one carrying a file
# nobody classified.
KIND_SCREENSHOT = "screenshot"
KIND_SIDECAR = "sidecar"
KIND_FAILURE_SCREENSHOT = "failure_screenshot"
KIND_COVERAGE_RECORD = "coverage_record"
KIND_COVERAGE_SOURCEMAP = "coverage_sourcemap"
KIND_COVERAGE_GAPS = "coverage_gaps"
KIND_FAILURE_LOG = "failure_log"
KIND_WORKER_LOG = "worker_log"
KIND_GUIDE_HTML = "guide_html"
KIND_GUIDE_MD = "guide_md"
KIND_INTERACTIVE_MANIFEST = "interactive_manifest"
KIND_GUIDE_ASSET = "guide_asset"
KIND_TRACE = "trace"
KIND_VIDEO = "video"
KIND_REPORT = "report"
KIND_EVENTS = "events"
KIND_JUNIT = "junit"
KIND_RUN_SIDECAR = "run_sidecar"
KIND_OTHER = "other"

#: Kinds a size budget may drop, cheapest-loss first. Guides are regenerable
#: from the screenshots the bundle still carries — that is precisely the point
#: of shipping the tree unrewritten — so they cost less to lose than a capture.
#:
#: Three kinds are deliberately absent, and the omission is the rule rather than
#: an oversight (this tuple reads as exhaustive, so it is worth stating): a
#: ``screenshot`` and its ``sidecar`` are the run's irreplaceable evidence, and
#: ``coverage_gaps`` is a few kilobytes that is the *only* way a detached bundle
#: can report a real coverage surface — the store it came from lives outside
#: ``output/`` and never travels.
DROP_ORDER = (
    KIND_VIDEO,
    KIND_TRACE,
    KIND_GUIDE_ASSET,
    KIND_GUIDE_HTML,
    KIND_GUIDE_MD,
    KIND_INTERACTIVE_MANIFEST,
    KIND_REPORT,
    KIND_OTHER,
    KIND_COVERAGE_SOURCEMAP,
    KIND_FAILURE_SCREENSHOT,
)

#: ``<role>_page__fr-mobile.json`` / ``<role>_page_noauth__default-default.json``
_COVERAGE_RECORD_RE = re.compile(r"^(?P<fixture>.+)__(?P<locale>[^-]*)-(?P<layout>[^-]*)\.json$")

_GUIDE_TEXT_EXT = {".html": KIND_GUIDE_HTML, ".md": KIND_GUIDE_MD}


@dataclass(frozen=True)
class ClassifyContext:
    """Everything the classifier needs that it cannot read off a path."""

    #: The run being bundled. Coverage and event directories are per-run, so
    #: this is what keeps a *previous* run's records out of this bundle.
    run_id: str
    locales: frozenset[str] = frozenset()
    layouts: frozenset[str] = frozenset()
    #: ``sanitise_nodeid(nodeid)`` → ``(nodeid, scenario, locale, layout)``, for
    #: the artifacts named after a nodeid. Built by :func:`nodeid_index`.
    nodeid_index: dict = field(default_factory=dict)
    #: Directory names under ``output/`` for each root, so a project that
    #: relocated one via ``config.output`` (screenshots, guides) or
    #: ``coverage.records_dir`` still classifies. Filled by
    #: :func:`steppy.bundle.create.resolve_roots`. ``reports_dirname`` has no
    #: config knob — ``output.reports_dir`` is deprecated and inert.
    screenshots_dirname: str = "screenshots"
    coverage_dirname: str = "coverage"
    guides_dirname: str = "guides"
    reports_dirname: str = "reports"


def nodeid_index(tests: Iterable) -> dict:
    """Forward map from sanitised nodeid to attribution.

    Built from the run's own test results, so it covers exactly the tests that
    ran — which is exactly the set that can have produced a failure screenshot,
    a trace or a video.
    """
    index = {}
    for test in tests:
        nodeid = getattr(test, "nodeid", None)
        if not nodeid:
            continue
        index[sanitise_nodeid(nodeid)] = (
            nodeid,
            getattr(test, "scenario", None),
            getattr(test, "locale", None),
            getattr(test, "layout", None),
        )
    return index


def _attribute_nodeid(stem: str, ctx: ClassifyContext) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """``(scenario, locale, layout)`` for a nodeid-named artifact.

    Failure screenshots carry a trailing ``__<fixture>`` the traces and videos
    do not, so try the whole stem first and then progressively shorter prefixes.
    An exact hit is authoritative; a miss returns all-``None`` rather than a
    guess, because the sanitiser is not invertible.
    """
    hit = ctx.nodeid_index.get(stem)
    if hit is not None:
        return hit[1], hit[2], hit[3]
    # Peel trailing "__<suffix>" groups (the fixture name on failure shots).
    prefix = stem
    while "__" in prefix:
        prefix = prefix.rsplit("__", 1)[0]
        hit = ctx.nodeid_index.get(prefix)
        if hit is not None:
            return hit[1], hit[2], hit[3]
    return None, None, None


def _literal_or_none(value: str, configured: frozenset[str]) -> Optional[str]:
    """Map the sentinel ``default`` back to ``None``.

    The coverage record writer substitutes the literal string ``default`` for an
    unset dimension, unlike the screenshot tree which omits the segment. A
    project *could* legitimately have a locale named ``default``, so only treat
    it as the sentinel when it is not one the project declares.
    """
    if not value:
        return None
    if value == "default" and value not in configured:
        return None
    return value


#: Gap report a run leaves beside its raw records, so a bundle can show real
#: coverage buckets. The sqlite store it is computed from lives outside
#: ``output/`` (deliberately — cleanup must not wipe manual triage) and so can
#: never be in an archive; this file is what crosses the gap.
COVERAGE_GAPS_NAME = "gaps.json"


def _coverage_file(rel: Path, parts: tuple[str, ...], ctx: ClassifyContext) -> Optional[BundleFile]:
    """``<coverage>/...`` — the gap report, raw records, gzipped source maps.

    ``None`` means **skip this file entirely**, and it is reserved for exactly one
    case: a *previous* run's raw records, which is what keeps an ``output/``
    directory that accumulates across runs from polluting today's bundle.

    Everything else under the coverage root gets a kind, even if that kind is
    ``other``. Returning ``None`` for an unrecognised name would silently drop a
    file — and this module's own rule is that carrying a file nobody labelled
    beats dropping one nobody noticed. Before this branch existed,
    ``output/coverage/gaps.json`` vanished from every bundle for that reason.
    """
    # parts: (<coverage>, "raw", <run_id>, ...) for a record; (<coverage>,
    # "gaps.json") for the gap report.
    if parts[1:] == (COVERAGE_GAPS_NAME,):
        return BundleFile(path="", kind=KIND_COVERAGE_GAPS, sha256="", size=0)
    if parts[1:2] != ("raw",) or len(parts) < 4:
        return BundleFile(path="", kind=KIND_OTHER, sha256="", size=0)
    if parts[2] != ctx.run_id:
        # A previous run's records. Excluding them is what makes an output/
        # directory that accumulates across runs harmless.
        return None
    tail = parts[3:]
    if tail[0] == "_maps":
        return BundleFile(path="", kind=KIND_COVERAGE_SOURCEMAP, sha256="", size=0)
    if len(tail) != 2:
        return BundleFile(path="", kind=KIND_OTHER, sha256="", size=0)
    scenario = tail[0]
    match = _COVERAGE_RECORD_RE.match(tail[1])
    locale = layout = None
    if match:
        locale = _literal_or_none(match.group("locale"), ctx.locales)
        layout = _literal_or_none(match.group("layout"), ctx.layouts)
    return BundleFile(
        path="",
        kind=KIND_COVERAGE_RECORD,
        sha256="",
        size=0,
        scenario=scenario,
        locale=locale,
        layout=layout,
    )


def _guide_file(parts: tuple[str, ...], ctx: ClassifyContext) -> BundleFile:
    """``<guides>/<role>/<locale>/[<layout>/]<slug>.{md,html}`` and friends."""
    name = parts[-1]
    suffix = Path(name).suffix.lower()
    if name == "guide.json":
        return BundleFile(path="", kind=KIND_INTERACTIVE_MANIFEST, sha256="", size=0, scenario=parts[-2])
    kind = _GUIDE_TEXT_EXT.get(suffix, KIND_GUIDE_ASSET)
    # parts = (<guides>, <role>, <locale>, [<layout>,] <file>)
    middle = parts[2:-1]
    locale = middle[0] if len(middle) >= 1 else None
    layout = middle[1] if len(middle) >= 2 else None
    scenario = Path(name).stem if kind in (KIND_GUIDE_HTML, KIND_GUIDE_MD) else None
    if scenario and scenario.endswith("-handbook"):
        scenario = None
    return BundleFile(
        path="",
        kind=kind,
        sha256="",
        size=0,
        scenario=scenario,
        locale=_literal_or_none(locale or "", ctx.locales) if locale else None,
        layout=layout,
    )


def classify(rel: Path, ctx: ClassifyContext) -> Optional[BundleFile]:
    """Classify one path relative to ``output/``.

    Returns a :class:`BundleFile` with ``path``/``sha256``/``size`` left blank
    for the caller to fill (it is the one doing I/O), or ``None`` when the file
    belongs to a different run and must be skipped entirely.
    """
    parts = rel.parts
    if not parts:
        return None
    root = parts[0]

    if root == ctx.screenshots_dirname:
        if len(parts) >= 2 and parts[1] == FAILURES_DIRNAME:
            scenario, locale, layout = _attribute_nodeid(Path(parts[-1]).stem, ctx)
            return BundleFile(
                path="",
                kind=KIND_FAILURE_SCREENSHOT,
                sha256="",
                size=0,
                scenario=scenario,
                locale=locale,
                layout=layout,
            )
        capture = classify_capture_path(parts[1:], locales=ctx.locales, layouts=ctx.layouts)
        if capture is None:
            return BundleFile(path="", kind=KIND_OTHER, sha256="", size=0)
        return BundleFile(
            path="",
            kind=KIND_SCREENSHOT if capture.ext == "png" else KIND_SIDECAR,
            sha256="",
            size=0,
            scenario=capture.scenario,
            locale=capture.locale,
            layout=capture.layout,
            step=capture.step,
            frame=capture.frame,
        )

    if root == ctx.coverage_dirname:
        return _coverage_file(rel, parts, ctx)

    if root == ctx.guides_dirname and len(parts) >= 2:
        return _guide_file(parts, ctx)

    if root == "failures" and len(parts) >= 2:
        if parts[1] == "_worker-logs":
            return BundleFile(path="", kind=KIND_WORKER_LOG, sha256="", size=0)
        return BundleFile(path="", kind=KIND_FAILURE_LOG, sha256="", size=0, scenario=parts[1])

    if root == "traces":
        scenario, locale, layout = _attribute_nodeid(Path(parts[-1]).stem, ctx)
        return BundleFile(path="", kind=KIND_TRACE, sha256="", size=0, scenario=scenario, locale=locale, layout=layout)

    if root == "videos":
        scenario, locale, layout = _attribute_nodeid(Path(parts[-1]).stem, ctx)
        return BundleFile(path="", kind=KIND_VIDEO, sha256="", size=0, scenario=scenario, locale=locale, layout=layout)

    if root == ctx.reports_dirname:
        return BundleFile(path="", kind=KIND_REPORT, sha256="", size=0)

    return BundleFile(path="", kind=KIND_OTHER, sha256="", size=0)


def count_coverage_records(coverage_root: Path, run_id: str) -> int:
    """How many raw coverage records this run produced, for ``manifest.coverage``."""
    raw = coverage_root / "raw" / run_id
    if not raw.is_dir():
        return 0
    return sum(1 for p in raw.rglob("*.json") if p.is_file() and p.parent.name != "_maps")


def read_coverage_record_attribution(path: Path) -> Optional[tuple]:
    """``(scenario, locale, layout)`` read from a coverage record's own JSON.

    Self-description beats filename parsing, so this is tried first and the
    regex is only the fallback for a truncated or unreadable record.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get("scenario"), data.get("locale"), data.get("layout")
