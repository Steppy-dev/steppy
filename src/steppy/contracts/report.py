"""``report.json`` — the run report's view model, as a versioned wire contract.

A results bundle says what a run *produced*. This says what a run *means*: which
scenarios passed, which step a failure died in, what the distinct failure causes
were, how much of the declared surface the suite reached. ``steppy report``
renders it to HTML; a control plane renders it its own way. That sharing is the
whole reason it is a contract rather than a private dict — the alternative is two
independently-written readers of the same bundle, and this repo has been burned by
duplicated definitions twice already.

Same compatibility charter as :mod:`steppy.contracts.bundle`, and for the same
reasons:

1. Every model is ``extra="allow"``. A newer producer's extra field must survive
   an older consumer, not crash it. (``models.py`` is ``extra="forbid"``, which is
   the right posture for a hand-written config where a typo must fail loudly, and
   the wrong one for a wire format.)
2. Unknown enum-ish values are data. ``outcome`` and ``status`` are plain ``str``
   — render what you get.
3. Consumers accept version N and N−1. Additive fields never bump the version.
4. Absent keys default. Never require a field a older producer could not have
   written.

**Two properties worth stating because code depends on them.**

``report.json`` carries **no render-time clock and no absolute paths**. The only
timestamp is the run's own ``created_at``, taken from the bundle. That makes the
file byte-reproducible for an unchanged bundle — so CI can diff two of them, and
so a test can assert on the model instead of on HTML. ``manifest.build`` refuses
``now`` for the same reason.

``capabilities`` is the **honesty block**. Per-step timelines exist only when the
run set ``STEPPY_STEP_EVENTS=1`` (which ``steppy run --bundle`` does), coverage
exists only if it was recorded, and assert *strength* is unknowable from a
detached bundle. Without somewhere to say so, a consumer cannot tell "no timeline
was recorded" from "the timeline was empty" — the same distinction ``truncated``
exists to make in the bundle manifest.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .bundle import BundleRun

#: Bump only on a breaking change. Additive fields never require it.
REPORT_SCHEMA_VERSION = 1

#: What a consumer at this version will read. The N and N−1 rule as data, so the
#: error message and the drift test read the same tuple.
ACCEPTED_REPORT_VERSIONS: tuple[int, ...] = (1,)


class _Wire(BaseModel):
    """Base for every wire model: unknown fields are kept, never rejected."""

    model_config = ConfigDict(extra="allow")


class ReportSourceInfo(_Wire):
    """Where the rendered run came from, for the page footer and for support.

    ``kind`` is ``tree`` (the project's live ``output/``), ``archive`` (a
    ``.tar.zst``/``.tar.gz``) or ``dir`` (an unpacked bundle). ``label`` is a
    *display* string — a bundle filename or a relative tree path, never an
    absolute one, because this file gets published.
    """

    kind: str
    label: str = ""


class ReportTotals(_Wire):
    """Run-level arithmetic.

    ``duration_ms_sum`` is the **sum of test durations**, not wall clock. Under
    ``--workers N`` those differ by roughly N, and a bundle carries no
    ``finished_at`` to compute the real elapsed time from — so the field is named
    for what it actually is rather than implying a number it cannot know.
    """

    tests: int = 0
    scenarios: int = 0
    scenarios_not_passed: int = 0
    outcomes: dict[str, int] = Field(default_factory=dict)
    duration_ms_sum: int = 0
    #: Worst outcome across the run, using the bundle's own vocabulary.
    verdict: str = "passed"
    #: What a gate *would* use. The ``steppy report`` command itself always exits
    #: 0 — it is a renderer, and its exit code describes rendering.
    exit_code: int = 0


class ReportCapabilities(_Wire):
    """What this report could and could not know. See the module docstring."""

    #: False when the run recorded no ``step-start`` events at all.
    step_timeline: bool = False
    #: True when at least one test has more than one recorded attempt.
    attempts: bool = False
    #: ``bundled`` | ``store`` | ``records`` | ``none`` — where coverage came from.
    coverage: str = "none"
    #: ``all`` | ``failed`` | ``none`` — which captures were copied into the site.
    screenshots: str = "none"
    #: False when assert *strength* had to be guessed (a detached bundle carries no
    #: ``scenarios/*.md``, so every covered node would read "weak").
    strength_reliable: bool = True


class ReportStep(_Wire):
    """One step of one attempt.

    ``step`` is the **scenario** step number and the sequence is **sparse**: a step
    skipped by ``only-in:`` or ``layouts:`` emits no events, so gaps are expected
    and must never be renumbered. There is no total-step count anywhere in a
    bundle, so this is "step 7", never "step 7 of 12".

    ``status`` is ``passed`` when the step closed and ``running`` when it opened and
    never did — which is precisely the failing step. ``duration_ms`` is ``None``
    there, because the end is unknown rather than zero.
    """

    step: int
    title: str = ""
    action: str = ""
    status: str = "passed"
    offset_ms: int = 0
    duration_ms: Optional[int] = None
    #: Site-relative URLs of the captures for this step, in frame order.
    screenshots: list[str] = Field(default_factory=list)


class ReportVariant(_Wire):
    """One (locale, layout) execution of one scenario.

    ``attempts`` is recoverable only for in-process pytest ``--reruns``: the
    parallel coordinator's retry pass runs a fresh process, which truncates the
    JSONL and takes attempt 1 with it. So ``1`` can mean "ran once" or "the earlier
    attempts were overwritten", and the report says which when it can.
    """

    nodeid: str
    locale: Optional[str] = None
    layout: Optional[str] = None
    outcome: str = "unknown"
    duration_ms: Optional[int] = None
    attempts: int = 1
    attempt_outcomes: list[str] = Field(default_factory=list)
    failed_step: Optional[int] = None
    failed_step_title: str = ""
    #: 8-hex group id from ``_parallel/fingerprint``, joining this to a cause.
    fingerprint: Optional[str] = None
    #: Decoded tail, at most 15 lines (``results.FAIL_TAIL_LINES``).
    fail_tail: str = ""
    steps: list[ReportStep] = Field(default_factory=list)
    failure_screenshot: Optional[str] = None
    #: Present in the run but NOT copied into the site — a trace needs
    #: ``playwright show-trace`` to open, so a link would be a dead end.
    trace_path: Optional[str] = None
    video_path: Optional[str] = None


class ReportScenario(_Wire):
    """One scenario and every variant of it that ran.

    ``outcome`` is the **worst** across variants: a flow that fails only in Dutch
    on mobile is a failing flow.
    """

    slug: str
    outcome: str = "unknown"
    outcomes: dict[str, int] = Field(default_factory=dict)
    duration_ms_sum: int = 0
    href: str = ""
    variants: list[ReportVariant] = Field(default_factory=list)
    #: Parallel runs only — ``_parallel/failures`` writes it, single-worker does not.
    failure_log: Optional[str] = None


class ReportFailureOccurrence(_Wire):
    scenario: str
    nodeid: str
    locale: Optional[str] = None
    layout: Optional[str] = None


class ReportFailureCause(_Wire):
    """One distinct failure, and everything that hit it.

    This is the primitive that makes a Steppy report readable where a JUnit report
    is not: forty scenarios dying from one broken selector collapse into one row
    instead of forty tracebacks. Grouping is ``_parallel/fingerprint``, unchanged —
    the same code the parallel console summary uses.
    """

    fingerprint: str
    headline: str = ""
    count: int = 0
    scenarios: list[str] = Field(default_factory=list)
    occurrences: list[ReportFailureOccurrence] = Field(default_factory=list)
    #: Full tail of the first occurrence only. The rest are the same failure.
    exemplar_tail: str = ""


class ReportBadge(_Wire):
    """The e2e-surface number, and the shields payload derived from it.

    ``percent is None`` means *not measured* — nothing declared — which renders as
    ``n/a`` in grey rather than ``0%``. A badge reading 0% invites someone to go
    fix a number that is not broken.
    """

    surface: str = "all"
    label: str = "e2e surface"
    tested: int = 0
    declared: int = 0
    ratio: Optional[float] = None
    percent: Optional[int] = None
    color: str = "lightgrey"


class ReportNonUi(_Wire):
    """How much of the declared surface was discounted, and on whose authority."""

    declared: int = 0
    tested: int = 0
    by_source: dict[str, int] = Field(default_factory=dict)


class ReportCoverage(_Wire):
    """The coverage section, or the reason there isn't one.

    ``tier`` records where the numbers came from: ``bundled`` (a
    ``coverage/gaps.json`` that travelled with the run), ``store`` (the project's
    live ``.steppy/coverage.db`` — live-tree input only, since reading it for
    someone else's bundle would attribute this machine's map to their run),
    ``records`` (rebuilt from raw records, with strength suppressed), or ``none``.
    """

    tier: str = "none"
    counts: dict[str, Any] = Field(default_factory=dict)
    e2e_surface: dict[str, Any] = Field(default_factory=dict)
    #: The gap buckets themselves, keyed ``endpoints``/``routes``/``modules`` —
    #: ``compute_gaps``' own output, carried verbatim rather than reshaped. A
    #: second shape for the same data is a second thing to keep in sync, and the
    #: bucket lists are already a documented part of ``coverage report --json``.
    buckets: dict[str, Any] = Field(default_factory=dict)
    non_ui: ReportNonUi = Field(default_factory=ReportNonUi)
    badge: ReportBadge = Field(default_factory=ReportBadge)
    href: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class ReportArtifactGroup(_Wire):
    count: int = 0
    bytes: int = 0


class ReportArtifacts(_Wire):
    """What the run produced, by kind — an inventory, not a copy.

    The bundle manifest is the file list; duplicating it here would double the
    size of every report for no reader.
    """

    files: int = 0
    bytes: int = 0
    #: Captures copied once despite appearing under several manifest paths.
    deduped: int = 0
    by_kind: dict[str, ReportArtifactGroup] = Field(default_factory=dict)


class ReportModel(_Wire):
    """The whole document. See the module docstring for the two invariants."""

    steppy_report: int = Field(description="Contract version; see ACCEPTED_REPORT_VERSIONS.")
    generated_by: str = ""
    source: ReportSourceInfo
    run: BundleRun
    totals: ReportTotals = Field(default_factory=ReportTotals)
    #: Locales and layouts actually **observed** in the test rows, not what
    #: ``run.spec`` asked for: a scenario deselected by a disjoint frontmatter
    #: ``layouts:`` never ran, and a column of empty cells reads as a failure.
    dimensions: dict[str, list[str]] = Field(default_factory=dict)
    capabilities: ReportCapabilities = Field(default_factory=ReportCapabilities)
    scenarios: list[ReportScenario] = Field(default_factory=list)
    failure_causes: list[ReportFailureCause] = Field(default_factory=list)
    coverage: ReportCoverage = Field(default_factory=ReportCoverage)
    artifacts: ReportArtifacts = Field(default_factory=ReportArtifacts)
    warnings: list[str] = Field(default_factory=list)
    #: Carried through from the bundle: the size budget dropped something that
    #: belonged. Never set for a flag-excluded artifact.
    truncated: bool = False


SCHEMA_TITLE = "Steppy run report model"


def generate_report_schema() -> dict:
    """JSON Schema for ``report.json``, for consumers that are not Python."""
    schema = ReportModel.model_json_schema()
    schema["title"] = SCHEMA_TITLE
    schema["x-steppy-report-version"] = REPORT_SCHEMA_VERSION
    schema["x-steppy-report-accepted"] = list(ACCEPTED_REPORT_VERSIONS)
    return schema


def schema_json() -> str:
    """The canonical serialized form written to disk and compared in tests."""
    import json

    return json.dumps(generate_report_schema(), indent=2, sort_keys=True) + "\n"


def accepts(version: int) -> bool:
    return version in ACCEPTED_REPORT_VERSIONS


def parse_report(payload: dict[str, Any]) -> ReportModel:
    """Validate a ``report.json`` payload, with actionable version errors."""
    version = payload.get("steppy_report")
    if not isinstance(version, int):
        raise ValueError("report.json is missing the integer 'steppy_report' contract version")
    if not accepts(version):
        supported = ", ".join(f"v{v}" for v in sorted(ACCEPTED_REPORT_VERSIONS, reverse=True))
        raise ValueError(
            f"report contract v{version} is not accepted by this version of steppy (supports {supported}). "
            "Upgrade the steppy that is reading this report, or re-render it with the steppy that wrote it."
        )
    return ReportModel.model_validate(payload)


def report_bytes(model: ReportModel) -> bytes:
    """``report.json`` as it lands on disk: sorted keys, nulls kept, trailing NL.

    ``exclude_none=False`` matters — the same reason it does for the bundle
    manifest. ``failed_step: null`` is a *real answer* (nothing was open when the
    test died), and dropping the key would make it indistinguishable from a
    producer that never recorded steps at all.
    """
    import json

    payload = model.model_dump(mode="json", exclude_none=False)
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
