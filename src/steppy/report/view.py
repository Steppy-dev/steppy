"""What the report's pages show, worded and ordered: :class:`RunView` from a :class:`ReportModel`.

The rules are those of Steppy Cloud's run page, so a local report and the same run in
the cloud read the same: the kind of each variant, the order of scenarios and of
failing variants, the cells of the matrix, the groups under a failure cause, the steps
of a variant. The pages only lay it out.

**One kind per variant**, worst first: ``f`` failing, ``k`` flaky and not failing,
``p`` passed, ``s`` skipped, ``n`` not run. Two things the cloud's page does not have
to handle are kept here rather than lost:

- **A variant without the run's locale or layout** (one scenario declares
  ``layouts:``, another does not). It gets a row or a column of its own, "default".
- **Several tests in one variant** (a tour's replay beside its scenario's test). The
  cell shows the worst of them, and the scenario's page shows each.

Like :mod:`steppy.report.model`, this imports no template engine and reads no file. A
screenshot's size arrives in ``sizes``, because an outline placed in percent of the
image needs it and a static page cannot measure it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, NamedTuple, Optional, Sequence

from ..contracts.report import (
    ReportCapture,
    ReportFailureCause,
    ReportModel,
    ReportScenario,
    ReportStep,
    ReportVariant,
)
from .links import cause_fragment, scenario_fragment, step_fragment, variant_fragment
from .model import scenario_href
from .words import (
    ci_label,
    counted,
    group_by_locales,
    in_order,
    join_list,
    locale_share,
    num,
    verdict_text,
    web_url,
    when_text,
    where_text,
)

#: Worst first. A cell, a row and a scenario take the worst kind they hold.
WORST = ("f", "k", "p", "s", "n")

#: The status each kind shows: an icon, a colour, a word.
STATUS = {"f": "failed", "k": "flaky", "p": "passed", "s": "skipped", "n": "not_run"}

#: How each status is worded, alone and after a count.
_WORDS = {
    "passed": "Passed",
    "failed": "Failed",
    "error": "Error",
    "interrupted": "Interrupted",
    "worker_died": "Worker died",
    "flaky": "Flaky",
    "skipped": "Skipped",
    "not_run": "Not run",
    "unknown": "Unknown",
}
_LOWER = {"f": "failed", "k": "flaky", "p": "passed", "s": "skipped", "n": "not run"}

#: Outcomes that say a test ran and did not pass, for the retry rule.
_FAIL_SIDE = frozenset({"failed", "error", "interrupted", "worker_died"})

#: A variant without the run's locale or layout.
DEFAULT_DIMENSION = "default"

#: Groups of a cause shown before "Show N more", and screenshots beside it.
GROUPS_SHOWN = 5
SHOTS_SHOWN = 3

#: A step whose run recorded no title writes "Step 7", which the page says itself.
_UNTITLED = re.compile(r"step\s*\d+", re.IGNORECASE)

#: Layouts with an icon of their own.
_LAYOUT_ICONS = frozenset({"desktop", "tablet", "mobile"})


# ── kinds and order ─────────────────────────────────────────────────────────


def is_failing(outcome: str) -> bool:
    """Not passed and not skipped, the report model's rule: an outcome nobody knows is not a pass."""
    return outcome not in ("passed", "skipped")


def retried(variant: ReportVariant) -> bool:
    """Its attempts disagree: one failed and another passed."""
    outcomes = variant.attempt_outcomes
    return "passed" in outcomes and any(o in _FAIL_SIDE for o in outcomes)


def kind_of(variant: Optional[ReportVariant]) -> str:
    if variant is None:
        return "n"
    if is_failing(variant.outcome):
        return "f"
    if retried(variant):
        return "k"
    return "s" if variant.outcome == "skipped" else "p"


def worst_of(kinds: Iterable[str]) -> str:
    """The worst of some kinds; ``n`` when there are none."""
    return min(kinds, key=WORST.index, default="n")


class Ref(NamedTuple):
    """One variant, by name: what a link carries."""

    scenario: str
    locale: Optional[str]
    layout: Optional[str]


class _Scenario:
    """A scenario and its variants by ``(locale, layout)``, the worst of each pair first."""

    def __init__(self, scenario: ReportScenario):
        self.slug = scenario.slug
        self.report = scenario
        self.by_key: dict[tuple, list[ReportVariant]] = {}
        for variant in scenario.variants:
            self.by_key.setdefault((variant.locale, variant.layout), []).append(variant)
        for rows in self.by_key.values():
            rows.sort(key=lambda v: (WORST.index(kind_of(v)), v.nodeid))

    def at(self, locale: Optional[str], layout: Optional[str]) -> Optional[ReportVariant]:
        rows = self.by_key.get((locale, layout))
        return rows[0] if rows else None

    def kind(self, locale: Optional[str], layout: Optional[str]) -> str:
        return kind_of(self.at(locale, layout))


class RunModel:
    """What ran where: the dimensions in the order the run declared them, and the failing variants."""

    def __init__(self, report: ReportModel):
        variants = [v for s in report.scenarios for v in s.variants]
        spec = report.run.spec
        # The dimensions are what ran; the spec is the order people wrote.
        self.locales = in_order(
            [*report.dimensions.get("locales", []), *(v.locale for v in variants if v.locale)], spec.locales
        )
        self.layouts = in_order(
            [*report.dimensions.get("layouts", []), *(v.layout for v in variants if v.layout)], spec.layouts
        )
        self.L: list[Optional[str]] = [*self.locales] or [None]
        self.Y: list[Optional[str]] = [*self.layouts] or [None]
        if self.locales and any(not v.locale for v in variants):
            self.L.append(None)
        if self.layouts and any(not v.layout for v in variants):
            self.Y.append(None)
        #: No locales and no layouts: one variant per scenario.
        self.flat = not self.locales and not self.layouts
        self.scenarios = [_Scenario(s) for s in report.scenarios]
        self._by_slug = {s.slug: s for s in self.scenarios}
        #: Every failing variant in table order: scenario, then layout, then locale.
        self.failing = [
            Ref(s.slug, locale, layout)
            for s in self.scenarios
            for layout in self.Y
            for locale in self.L
            if s.kind(locale, layout) == "f"
        ]

    def locale_name(self, locale: Optional[str]) -> str:
        return locale or (DEFAULT_DIMENSION if self.locales else "")

    def layout_name(self, layout: Optional[str]) -> str:
        return layout or (DEFAULT_DIMENSION if self.layouts else "")

    @property
    def locale_names(self) -> list[str]:
        return [self.locale_name(v) for v in self.L] if self.locales else []

    @property
    def layout_names(self) -> list[str]:
        return [self.layout_name(v) for v in self.Y] if self.layouts else []

    def where(self, refs: Iterable) -> str:
        pairs = [(self.locale_name(r.locale), self.layout_name(r.layout)) for r in refs]
        return where_text(pairs, self.locale_names, self.layout_names)

    def label(self, locale: Optional[str], layout: Optional[str]) -> str:
        """``fr · mobile``, ``fr``, or ``""`` for a variant with neither."""
        return " · ".join(p for p in (locale, layout) if p)

    def scenario(self, slug: str) -> Optional[_Scenario]:
        return self._by_slug.get(slug)

    def worst(self, scenario: _Scenario) -> str:
        return worst_of(scenario.kind(locale, layout) for layout in self.Y for locale in self.L)

    def table(self) -> list[tuple[_Scenario, str]]:
        """Failing scenarios first, then flaky, passed, skipped, not run; the report's order inside each."""
        rows = [(s, self.worst(s)) for s in self.scenarios]
        return sorted(rows, key=lambda row: WORST.index(row[1]))

    def _first(self, scenario: _Scenario, want: Optional[str] = None) -> Optional[Ref]:
        for layout in self.Y:
            for locale in self.L:
                kind = scenario.kind(locale, layout)
                if kind != "n" and (want is None or kind == want):
                    return Ref(scenario.slug, locale, layout)
        return None

    def scenario_ref(self, scenario: _Scenario) -> Optional[Ref]:
        """What a scenario named without its variant opens: its first failing one, else its first."""
        return self._first(scenario, "f") or self._first(scenario)

    def default_ref(self) -> Optional[Ref]:
        """The first failing variant in table order; else the first variant of the first scenario."""
        if self.failing:
            return self.failing[0]
        for scenario, _worst in self.table():
            ref = self._first(scenario)
            if ref:
                return ref
        return None

    def variant(self, ref: Optional[Ref]) -> Optional[ReportVariant]:
        scenario = self.scenario(ref.scenario) if ref else None
        return scenario.at(ref.locale, ref.layout) if scenario and ref else None


# ── screenshots ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ShotView:
    """One screenshot, with the outlines to draw on it as percentages of the image."""

    url: str
    alt: str = ""
    #: The image's own pixels, when known.
    width: Optional[int] = None
    height: Optional[int] = None
    #: ``left``, ``top``, ``width``, ``height`` of each outline, CSS percentages.
    marks: tuple[dict[str, str], ...] = ()
    #: Under the image, on a page that shows several.
    caption: str = ""


def _percent(value: float) -> str:
    # The box that clips the outlines is the image, so a target far outside it needs
    # no exact place, and a number nobody can draw must not reach the page.
    return f"{min(max(value, -200.0), 300.0):.4f}".rstrip("0").rstrip(".")


def marks_for(capture: Optional[ReportCapture], size: Optional[tuple[int, int]]) -> tuple[dict[str, str], ...]:
    """The outlines of one capture's boxes, in percent of the image's CSS size.

    The CSS size is the image's own size over the scale factor: the box is in CSS
    pixels of the captured image. Without the size there is nothing to place, so
    nothing is drawn.
    """
    if capture is None or size is None or not capture.boxes:
        return ()
    scale = capture.device_scale_factor or 1.0
    if scale <= 0 or size[0] <= 0 or size[1] <= 0:
        return ()
    width, height = size[0] / scale, size[1] / scale
    return tuple(
        {
            "left": _percent(box.x / width * 100),
            "top": _percent(box.y / height * 100),
            "width": _percent(box.width / width * 100),
            "height": _percent(box.height / height * 100),
        }
        for box in capture.boxes
        if box.x < width and box.y < height and box.x + box.width > 0 and box.y + box.height > 0
    )


# ── variants and steps ──────────────────────────────────────────────────────


@dataclass
class StepView:
    n: int
    anchor: str
    href: str
    title: str
    action: str
    #: ``p`` passed, ``f`` failed here.
    kind: str
    offset_ms: Optional[int] = None
    duration_ms: Optional[int] = None
    #: False for a failing step the run recorded no boundary of.
    recorded: bool = True
    #: The one the run page shows: the failure's on the failing step, else the first.
    thumb: Optional[ShotView] = None
    #: Every capture of the step, in frame order.
    shots: list[ShotView] = field(default_factory=list)

    @property
    def status(self) -> str:
        return STATUS[self.kind]


@dataclass
class VariantView:
    ref: Ref
    kind: str
    #: ``fr · mobile``, or ``default``.
    label: str
    #: The same, led by `` · ``, for a heading; ``""`` for a run without dimensions.
    where: str
    #: The element's id: the link's own fragment.
    anchor: str
    #: The link, relative to the site's root.
    href: str
    scenario_href: str
    status: str
    status_word: str
    attempts: str
    nodeid: str
    duration_ms: Optional[int] = None
    steps: list[StepView] = field(default_factory=list)
    failed_step: Optional[int] = None
    failed_step_title: str = ""
    failure_shot: Optional[ShotView] = None
    fail_tail: str = ""
    flaky_note: str = ""
    #: ``index.html#cause/…`` without the page, and the fingerprint it names.
    cause: Optional[str] = None
    fingerprint: Optional[str] = None
    counter: str = ""
    previous: Optional[str] = None
    next: Optional[str] = None
    traces: list[str] = field(default_factory=list)
    videos: list[str] = field(default_factory=list)
    #: A second test in the same variant: the link names the first one.
    extra: bool = False
    #: A frame that fits the layout: ``phone``, ``tablet`` or ``desktop``.
    frame: str = "desktop"

    @property
    def has_marks(self) -> bool:
        """Whether a scenario's page has an outline to hide for this variant."""
        shots = [s for step in self.steps for s in step.shots]
        return any(s.marks for s in [*shots, *([self.failure_shot] if self.failure_shot else [])])

    @property
    def has_thumb_marks(self) -> bool:
        """The same on the run page, which shows one screenshot per step."""
        shots = [step.thumb for step in self.steps if step.thumb]
        return any(s.marks for s in [*shots, *([self.failure_shot] if self.failure_shot else [])])

    @property
    def stopped_at(self) -> Optional[int]:
        """The step a failing variant stopped at, when one is marked."""
        return next((s.n for s in self.steps if s.kind == "f"), None)


def step_title(title: Optional[str]) -> str:
    """A step's own title, or ``""`` when the run recorded none."""
    return title if title and not _UNTITLED.fullmatch(title.strip()) else ""


def status_word(status: str) -> str:
    """``Failed``, ``Not run``. An outcome nobody knows is shown as it is."""
    return _WORDS.get(status, status)


def _frame(layout: Optional[str]) -> str:
    return {"mobile": "phone", "tablet": "tablet"}.get(layout or "", "desktop")


class _Builder:
    """Builds the views of one report."""

    def __init__(self, report: ReportModel, sizes: Optional[dict[str, tuple[int, int]]] = None):
        self.report = report
        self.sizes = sizes or {}
        self.model = RunModel(report)
        #: How a variant nobody ran reads: in a run that recorded no outcome at all,
        #: "not run" would be a claim.
        self.not_run = status_word("not_run" if report.totals.tests else "unknown")
        self.hrefs = {s.slug: s.href or scenario_href(s.slug) for s in report.scenarios}
        self._position = {ref: i for i, ref in enumerate(self.model.failing)}

    # Links.

    def href(self, ref: Ref) -> str:
        return f"{self.hrefs[ref.scenario]}#{variant_fragment(*ref)}"

    def step_href(self, ref: Ref, step: int) -> str:
        return f"{self.hrefs[ref.scenario]}#{step_fragment(*ref, step)}"

    # Screenshots.

    def shot(self, url: Optional[str], step: Optional[ReportStep], alt: str, caption: str = "") -> Optional[ShotView]:
        """A screenshot by its URL, with the boxes of the step's own capture of it.

        Looked up in the step itself: two steps with the same bytes share a URL.
        """
        if not url:
            return None
        capture = next((c for c in step.captures if c.url == url), None) if step else None
        return self._shot(url, capture, alt, caption)

    def _shot(self, url: str, capture: Optional[ReportCapture], alt: str, caption: str = "") -> ShotView:
        size = self.sizes.get(url)
        return ShotView(
            url=url,
            alt=alt,
            width=size[0] if size else None,
            height=size[1] if size else None,
            marks=marks_for(capture, size),
            caption=caption,
        )

    @staticmethod
    def failure_url(variant: Optional[ReportVariant]) -> Optional[str]:
        """The capture at the failure: the variant's own, else the failing step's last frame."""
        if variant is None:
            return None
        if variant.failure_screenshot:
            return variant.failure_screenshot
        step = next((s for s in variant.steps if s.step == variant.failed_step), None)
        return next((u for u in reversed(step.screenshots) if u), None) if step else None

    # Steps.

    def steps(self, ref: Ref, variant: ReportVariant) -> list[StepView]:
        failing = is_failing(variant.outcome)
        failed_at = variant.failed_step
        if failing and failed_at is None:
            failed_at = next((s.step for s in variant.steps if s.status == "running"), None)
        rows = sorted(variant.steps, key=lambda s: s.step)
        recorded = {s.step for s in rows}
        if failing and failed_at is not None and failed_at not in recorded:
            rows.append(ReportStep(step=failed_at, title=variant.failed_step_title, status="running"))
        views = []
        for step in rows:
            failed = failing and step.step == failed_at
            alt = f"Step {step.step}"
            url = self.failure_url(variant) if failed else next((u for u in step.screenshots if u), None)
            many = len(step.captures or step.screenshots) > 1
            shots = [
                self._shot(c.url, c, alt, f"Frame {i}" if many else "")
                for i, c in enumerate(step.captures or [ReportCapture(url=u) for u in step.screenshots], start=1)
            ]
            views.append(
                StepView(
                    n=step.step,
                    anchor=step_fragment(*ref, step.step),
                    href=self.step_href(ref, step.step),
                    title=step_title(step.title),
                    action=step.action,
                    kind="f" if failed else "p",
                    offset_ms=step.offset_ms,
                    duration_ms=step.duration_ms,
                    recorded=step.step in recorded,
                    thumb=self.shot(url, step, "Page at the moment of failure" if failed else alt),
                    shots=shots,
                )
            )
        return views

    # Variants.

    def variant(self, ref: Ref, variant: ReportVariant, *, extra: bool = False) -> VariantView:
        kind = kind_of(variant)
        label = self.model.label(ref.locale, ref.layout)
        failing_step = next((s for s in variant.steps if s.step == variant.failed_step), None)
        status = variant.outcome if kind == "f" else STATUS[kind]
        word = "Passed on retry" if kind == "k" else status_word(status)
        position = self._position.get(ref, -1)
        total = len(self.model.failing)
        if not total:
            counter = "No failing variant in this run"
        elif position < 0:
            counter = f"Not a failing variant · {num(total)} failing in this run"
        else:
            counter = f"Failing variant {num(position + 1)} of {num(total)}"
        following = position + 1 if position + 1 < total else None
        return VariantView(
            ref=ref,
            kind=kind,
            label=label or DEFAULT_DIMENSION,
            where=f" · {label}" if label else "",
            anchor=variant_fragment(*ref),
            href=self.href(ref),
            scenario_href=self.hrefs[ref.scenario],
            status=status,
            status_word=word,
            attempts=", ".join(self._attempts(variant)),
            nodeid=variant.nodeid,
            duration_ms=variant.duration_ms,
            steps=self.steps(ref, variant),
            failed_step=variant.failed_step,
            failed_step_title=step_title(variant.failed_step_title),
            failure_shot=self.shot(self.failure_url(variant), failing_step, "Page at the moment of failure")
            if kind == "f"
            else None,
            fail_tail=variant.fail_tail if kind == "f" else "",
            flaky_note=self._flaky_note(variant) if kind == "k" else "",
            cause=cause_fragment(variant.fingerprint) if kind == "f" and variant.fingerprint else None,
            fingerprint=variant.fingerprint,
            counter=counter,
            previous=self.href(self.model.failing[position - 1]) if position > 0 else None,
            next=self.href(self.model.failing[following]) if following is not None else None,
            traces=list(variant.trace_paths) or ([variant.trace_path] if variant.trace_path else []),
            videos=list(variant.video_paths) or ([variant.video_path] if variant.video_path else []),
            extra=extra,
            frame=_frame(ref.layout),
        )

    @staticmethod
    def _attempts(variant: ReportVariant) -> list[str]:
        return [status_word(o).lower() for o in variant.attempt_outcomes or [variant.outcome]]

    def _flaky_note(self, variant: ReportVariant) -> str:
        words = [f"attempt {i} {word}" for i, word in enumerate(self._attempts(variant), start=1)]
        return f"{', '.join(words).capitalize()}. Passed on retry in this run."

    def all_variants(self, scenario: _Scenario) -> list[VariantView]:
        """Every variant that ran, failing first, then flaky, passed and skipped; layout, then locale inside."""
        views = []
        for layout in self.model.Y:
            for locale in self.model.L:
                for i, variant in enumerate(scenario.by_key.get((locale, layout), [])):
                    views.append(self.variant(Ref(scenario.slug, locale, layout), variant, extra=i > 0))
        return sorted(views, key=lambda v: WORST.index(v.kind))


# ── the matrix ──────────────────────────────────────────────────────────────


@dataclass
class CellView:
    """One cell: a variant's status, or a layout's over every locale."""

    kind: str
    #: ``Failed``, ``11 failed``, ``Not run``.
    label: str
    layout: str
    #: What a screen reader hears before the cell's own words.
    spoken: str = ""
    #: The rest of a summary cell's counts, for a screen reader: ``, 15 passed``.
    more: str = ""
    href: Optional[str] = None

    @property
    def status(self) -> str:
        return STATUS[self.kind]

    @property
    def word(self) -> str:
        return status_word(self.status)


@dataclass
class LineView:
    locale: str
    cells: list[CellView]


@dataclass
class RowView:
    """One scenario of the matrix."""

    slug: str
    href: str
    kind: str
    where: str
    cells: list[CellView]
    #: More than one locale: the row opens into its locales.
    expandable: bool = False
    #: Failing and flaky locales, then the rest folded into one line.
    lines: list[LineView] = field(default_factory=list)
    folded: list[LineView] = field(default_factory=list)
    fold_label: str = ""

    @property
    def not_passing(self) -> bool:
        return self.kind in ("f", "k")


@dataclass
class ColumnView:
    label: str
    icon: Optional[str] = None


@dataclass
class MatrixView:
    columns: list[ColumnView]
    rows: list[RowView]
    note: str
    not_passing: int

    @property
    def total(self) -> int:
        return len(self.rows)


def _matrix(b: _Builder) -> MatrixView:
    m = b.model
    if m.flat:
        note = "One outcome per scenario. Open one to see its steps."
    elif not m.locales:
        note = "One outcome per layout. Open one to see its steps."
    elif len(m.L) == 1:
        note = f"Each cell is one variant in {m.L[0]}. Open one to see its steps."
    else:
        note = f"Each cell sums up a layout over all {num(len(m.L))} locales. Open a row for its locales."
    rows = [_row(b, scenario, worst) for scenario, worst in m.table()]
    return MatrixView(
        columns=[ColumnView(m.layout_name(y) or "Result", y if y in _LAYOUT_ICONS else None) for y in m.Y],
        rows=rows,
        note=note,
        not_passing=sum(1 for r in rows if r.not_passing),
    )


def _row(b: _Builder, scenario: _Scenario, worst: str) -> RowView:
    m = b.model
    single = len(m.L) == 1
    cells = []
    for layout in m.Y:
        kinds = [scenario.kind(locale, layout) for locale in m.L]
        kind = worst_of(kinds)
        ref = Ref(scenario.slug, m.L[0], layout)
        counts = [f"{num(kinds.count(k))} {_LOWER[k]}" for k in WORST if k in kinds]
        cells.append(
            CellView(
                kind=kind,
                label=b.not_run if kind == "n" else status_word(STATUS[kind]) if single else counts[0],
                layout=m.layout_name(layout),
                more="" if single or kind == "n" else "".join(f", {c}" for c in counts[1:]),
                href=b.href(ref) if single and kind != "n" else None,
            )
        )
    row = RowView(
        slug=scenario.slug,
        href=b.hrefs[scenario.slug],
        kind=worst,
        where=_row_where(m, scenario),
        cells=cells,
        expandable=not single,
    )
    if not single:
        bad: list[tuple[int, LineView]] = []
        for locale in m.L:
            line = _line(b, scenario, locale)
            kinds = [c.kind for c in line.cells]
            if "f" in kinds or "k" in kinds:
                bad.append((0 if "f" in kinds else 1, line))
            else:
                row.folded.append(line)
        row.lines = [line for _rank, line in sorted(bad, key=lambda item: item[0])]
        row.fold_label = _fold_label(row.folded)
    return row


def _line(b: _Builder, scenario: _Scenario, locale: Optional[str]) -> LineView:
    m = b.model
    cells = []
    for layout in m.Y:
        kind = scenario.kind(locale, layout)
        name = " · ".join(p for p in (m.locale_name(locale), m.layout_name(layout)) if p)
        cells.append(
            CellView(
                kind=kind,
                label=_LOWER[kind],
                layout=m.layout_name(layout),
                spoken=f"{scenario.slug}, {name}: " if name else f"{scenario.slug}: ",
                href=b.href(Ref(scenario.slug, locale, layout)) if kind != "n" else None,
            )
        )
    return LineView(m.locale_name(locale), cells)


def _fold_label(lines: Sequence[LineView]) -> str:
    if not lines:
        return ""
    not_run = any(c.kind == "n" for line in lines for c in line.cells)
    one = len(lines) == 1
    tail = f"wherever {'it' if one else 'they'} ran" if not_run else "everywhere"
    return f"{counted('locale', len(lines))} passed {tail}"


def _row_where(m: RunModel, scenario: _Scenario) -> str:
    """``Fails on mobile in fr and de · Flaky on mobile in en``, under a scenario's name."""
    if m.flat:
        variant = scenario.at(None, None)
        kind = kind_of(variant)
        if kind == "f" and variant is not None:
            return f"Failed at step {variant.failed_step}" if variant.failed_step else status_word(variant.outcome)
        return "Passed on retry" if kind == "k" else ""
    parts = []
    for kind, verb in (("f", "Fails"), ("k", "Flaky")):
        refs = [Ref(scenario.slug, *key) for key in scenario.by_key if scenario.kind(*key) == kind]
        if refs:
            parts.append(f"{verb} {m.where(refs)}".strip())
    return " · ".join(parts)


# ── failure causes ──────────────────────────────────────────────────────────


@dataclass
class ChipView:
    name: str
    layout: str
    href: str


@dataclass
class CauseGroupView:
    scenario: str
    where: str
    count: str
    chips: list[ChipView]


@dataclass
class CauseShotView:
    shot: ShotView
    name: str
    layout: str


@dataclass
class CauseView:
    anchor: str
    fingerprint: str
    headline: str
    line: str
    groups: list[CauseGroupView]
    shots: list[CauseShotView]
    tail: str = ""

    @property
    def shown(self) -> list[CauseGroupView]:
        return self.groups[:GROUPS_SHOWN]

    @property
    def hidden(self) -> list[CauseGroupView]:
        return self.groups[GROUPS_SHOWN:]


def _cause(b: _Builder, cause: ReportFailureCause) -> CauseView:
    m = b.model
    order = {s.slug: i for i, s in enumerate(m.scenarios)}
    places: dict[str, dict[str, list[str]]] = {}
    known: dict[tuple[str, str, str], Ref] = {}
    for o in cause.occurrences:
        if o.scenario not in b.hrefs:
            continue
        locale, layout = m.locale_name(o.locale), m.layout_name(o.layout)
        by_layout = places.setdefault(o.scenario, {})
        by_layout.setdefault(layout, []).append(locale)
        known[(o.scenario, locale, layout)] = Ref(o.scenario, o.locale or None, o.layout or None)
    groups, refs = [], []
    for slug in sorted(places, key=lambda s: order.get(s, len(order))):
        for layouts, locales in group_by_locales(places[slug], m.layout_names, m.locale_names):
            chosen = [known[(slug, locale, layout)] for layout in layouts for locale in locales]
            refs.extend(chosen)
            groups.append(_cause_group(b, slug, [y for y in layouts if y], [x for x in locales if x], chosen))
    first = next((v for v in map(m.variant, refs) if v is not None), None)
    step = first.failed_step if first else None
    title = step_title(first.failed_step_title) if first else ""
    hits = counted("scenario", len(places))
    if not m.flat:
        hits = f"{counted('variant', len(refs))} in {hits}"
    parts = [f"Hits {hits}"]
    if step:
        parts.append(f"failing at step {step}, {title}" if title else f"failing at step {step}")
    shots = []
    for ref in refs:
        url = b.failure_url(m.variant(ref))
        if url:
            label = m.label(ref.locale, ref.layout)
            alt = f"At the failure, {label}" if label else f"At the failure, {ref.scenario}"
            shots.append(CauseShotView(b._shot(url, None, alt), ref.locale or ref.scenario, ref.layout or ""))
    return CauseView(
        anchor=cause_fragment(cause.fingerprint),
        fingerprint=cause.fingerprint,
        headline=cause.headline,
        line=" · ".join([*parts, "fingerprint"]),
        groups=groups,
        shots=shots[:SHOTS_SHOWN],
        tail=cause.exemplar_tail,
    )


def _cause_group(b: _Builder, slug: str, layouts: list[str], locales: list[str], refs: list[Ref]) -> CauseGroupView:
    m = b.model
    if m.flat:
        variant = m.variant(refs[0]) if refs else None
        where = f" · at step {variant.failed_step}" if variant and variant.failed_step else ""
        count = counted("scenario", 1)
    else:
        parts = [join_list(layouts), locale_share(locales, m.locale_names) if locales else ""]
        where = "".join(f" · {p}" for p in parts if p)
        count = counted("variant", len(refs))
    return CauseGroupView(
        scenario=slug,
        where=where,
        count=count,
        chips=[ChipView(m.locale_name(r.locale) or r.scenario, m.layout_name(r.layout), b.href(r)) for r in refs],
    )


# ── the run ─────────────────────────────────────────────────────────────────


@dataclass
class CountView:
    status: str
    words: str


@dataclass
class HeaderView:
    """Which run, its verdict, its totals, and where it came from."""

    verdict: str
    counts: list[CountView]
    of: str
    branch: Optional[str] = None
    change: str = ""
    base_branch: Optional[str] = None
    commit: str = ""
    sha: Optional[str] = None
    tested_as: str = ""
    tested_sha: Optional[str] = None
    dirty: bool = False
    environment: Optional[str] = None
    ci: str = ""
    #: Set only when it may be a link (http or https).
    ci_url: Optional[str] = None
    #: The recorded address, shown as text, when it may not.
    ci_text: str = ""
    source: str = ""
    when: str = ""
    created_at: str = ""
    shape: str = ""


def _header(b: _Builder) -> HeaderView:
    report, m, run = b.report, b.model, b.report.run
    kinds = [kind_of(v) for s in report.scenarios for v in s.variants]
    failing = sum(1 for s in m.scenarios if m.worst(s) == "f")
    counts = [
        CountView(STATUS[k], f"{num(kinds.count(k))} {_LOWER[k]}")
        for k in ("f", "k", "p", "s")
        if kinds.count(k) or k == "p"
    ]
    unit = "scenario" if m.flat else "variant"
    shape = " × ".join(
        counted(noun, len(values)) for noun, values in (("locale", m.locales), ("layout", m.layouts)) if values
    )
    ci = run.ci
    url = web_url(ci.url) if ci else None
    git = run.git
    change = ""
    if git.pr:
        gitlab = ci is not None and ci.provider == "gitlab-ci"
        change = f"MR !{git.pr}" if gitlab else f"PR #{git.pr}"
    return HeaderView(
        verdict=verdict_text(
            tests=report.totals.tests,
            scenarios=len(m.scenarios),
            failing=failing,
            causes=len(report.failure_causes),
        ),
        counts=counts if kinds else [],
        of=f"of {counted(unit, len(kinds))}" if kinds else "",
        branch=git.branch,
        change=change,
        base_branch=git.base_branch,
        commit=git.sha[:7] if git.sha else "",
        sha=git.sha,
        tested_as=git.tested_sha[:7] if git.tested_sha else "",
        tested_sha=git.tested_sha,
        dirty=git.dirty is True,
        environment=run.environment,
        ci=ci_label(ci.provider, ci.url) if ci else "",
        ci_url=url,
        ci_text=ci.url if ci and ci.url and not url else "",
        source={"ci": "from CI", "local": "from a local machine"}.get(run.executor, run.executor),
        when=when_text(run.created_at),
        created_at=run.created_at,
        shape=shape or ("No locales or layouts" if kinds else ""),
    )


@dataclass
class ScenarioView:
    """One scenario's page."""

    slug: str
    href: str
    kind: str
    where: str
    size: str
    variants: list[VariantView]
    columns: list[ColumnView]
    #: Locales by layouts: failing locales first, then flaky, then the rest (folded from three).
    lines: list[LineView]
    folded: list[LineView]
    fold_label: str
    counts: list[CountView]
    duration_ms: int
    failure_log: Optional[str]
    #: ``Failed``, ``Passed``…
    word: str = ""
    #: The variant ``#<scenario>`` opens, and its id on the page.
    first: Optional[str] = None
    first_label: str = ""

    @property
    def status(self) -> str:
        return STATUS[self.kind]

    @property
    def has_marks(self) -> bool:
        return any(v.has_marks for v in self.variants)


def _scenario(b: _Builder, scenario: _Scenario) -> ScenarioView:
    m = b.model
    variants = b.all_variants(scenario)
    rows = [_line(b, scenario, locale) for locale in m.L]

    def rank(line: LineView) -> int:
        kinds = [c.kind for c in line.cells]
        return 0 if "f" in kinds else 1 if "k" in kinds else 2

    passing = [line for line in rows if rank(line) == 2]
    fold = len(passing) >= 3
    lines = sorted([line for line in rows if not fold or rank(line) < 2], key=rank)
    kinds = [v.kind for v in variants]
    where = _row_where(m, scenario)
    if not where and kinds:
        all_run = all(scenario.kind(locale, layout) != "n" for locale in m.L for layout in m.Y)
        where = "Passes in every variant" if all_run else "Passes wherever it runs"
        if set(kinds) == {"s"}:
            where = "Skipped"
    missing = [m.layout_name(y) for y in m.Y if m.layouts and all(scenario.kind(x, y) == "n" for x in m.L)]
    if missing and kinds:
        where = f"{where}; not run on {join_list(missing)}"
    shape = " × ".join(
        counted(noun, len(values)) for noun, values in (("locale", m.locales), ("layout", m.layouts)) if values
    )
    first = m.scenario_ref(scenario)
    report = scenario.report
    return ScenarioView(
        slug=scenario.slug,
        href=b.hrefs[scenario.slug],
        kind=m.worst(scenario),
        where=where,
        size=", ".join(p for p in (shape, counted("variant", len(kinds))) if p),
        variants=variants,
        columns=[ColumnView(m.layout_name(y), y if y in _LAYOUT_ICONS else None) for y in m.Y],
        lines=lines,
        folded=passing if fold else [],
        fold_label=_fold_label(passing) if fold else "",
        counts=[CountView(STATUS[k], f"{num(kinds.count(k))} {_LOWER[k]}") for k in ("f", "k", "p", "s") if k in kinds],
        duration_ms=report.duration_ms_sum,
        failure_log=report.failure_log,
        word=b.not_run if not kinds else status_word(STATUS[m.worst(scenario)]),
        first=variant_fragment(*first) if first else None,
        first_label=" · ".join(p for p in (scenario.slug, m.label(first.locale, first.layout)) if p) if first else "",
    )


@dataclass
class RunView:
    """Everything the pages lay out."""

    header: HeaderView
    warnings: list[str]
    causes: list[CauseView]
    matrix: MatrixView
    #: The first failing variant, else the first one; ``None`` for a run without any.
    variant: Optional[VariantView]
    scenarios: list[ScenarioView]
    flat: bool
    #: Whether the run has locales: the scenario page's table then names them.
    has_locales: bool
    has_layouts: bool

    def links(self) -> dict:
        """What the index page's link script needs: each scenario's page, and what it shows itself."""
        shown = self.variant
        return {
            "page": "index",
            "scenarios": {s.slug: s.href for s in self.scenarios},
            "shown": f"{shown.ref.scenario}{shown.where}" if shown else "",
        }

    @staticmethod
    def scenario_links(scenario: ScenarioView) -> dict:
        """What a scenario page's link script needs."""
        return {
            "page": "scenario",
            "scenario": scenario.slug,
            "anchor": scenario_fragment(scenario.slug),
            "first": scenario.first or "",
            "shown": scenario.first_label,
            "index": "../index.html",
        }


def build_view(report: ReportModel, *, sizes: Optional[dict[str, tuple[int, int]]] = None) -> RunView:
    """The pages' content. *sizes* is ``{url: (width, height)}`` of the images, in their own pixels."""
    b = _Builder(report, sizes)
    ref = b.model.default_ref()
    shown = b.model.variant(ref)
    return RunView(
        header=_header(b),
        warnings=[*report.warnings, *report.coverage.warnings],
        causes=[_cause(b, c) for c in report.failure_causes],
        matrix=_matrix(b),
        variant=b.variant(ref, shown) if ref and shown else None,
        scenarios=[_scenario(b, s) for s in b.model.scenarios],
        flat=b.model.flat,
        has_locales=bool(b.model.locales),
        has_layouts=bool(b.model.layouts),
    )
