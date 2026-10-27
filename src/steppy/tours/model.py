"""The compiled intermediate representation and the replay hand-off.

Pure data: frozen dataclasses with no behaviour beyond small lookups, so the
inference, text, emission and replay layers can be tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

#: ``setup`` runs before the tour starts, ``after`` once it completed (past
#: ``tour: end``); ``tour``/``run`` are the range; ``drop`` never runs.
Disposition = Literal["setup", "tour", "run", "drop", "after"]


@dataclass
class TourStepPlan:
    """One step of a tour, before text resolution. ``step_id`` is set by ``ids``."""

    step_index: int  # index into scenario.steps
    step_number: int
    step_title: str
    action: str
    target: Optional[str]  # test-id value, or None for a centred step
    source_selector: Optional[str]  # authored selector CI acts on (None when centred)
    advance: str
    wait_for: Optional[str] = None
    placement: Optional[str] = None
    hold: Optional[str] = None  # test-id of the element the popover must not cover
    target_click: Optional[str] = None  # info steps only: `pass` / `next` / `block`, None = the host's default
    layouts: Optional[tuple[str, ...]] = None
    field: Optional[str] = None  # fill-form: the `fields` key this sub-step fills
    step_id: str = ""


@dataclass(frozen=True)
class ReplayTourStep:
    """A tour step as the replay test sees it."""

    step_id: str
    index: int  # 0-based position in the tour (identical in every locale)
    target: Optional[str]
    wait_for: Optional[str]
    advance: str
    layouts: Optional[tuple[str, ...]]
    field: Optional[str] = None
    source_selector: Optional[str] = None
    hold: Optional[str] = None


@dataclass(frozen=True)
class ReplayEntry:
    """What the replay does with one scenario step (aligned 1:1 with scenario.steps)."""

    step_number: int
    disposition: Disposition
    reason: str
    tour_steps: tuple[ReplayTourStep, ...] = ()
    reloads_page: bool = False


@dataclass(frozen=True)
class ReplayPlan:
    """Everything the replay renderer needs; produced by the compiler."""

    scenario_slug: str
    tour_id: str
    role: str
    auth: bool
    default_locale: str
    locales: tuple[str, ...]
    entry_route: Optional[str]
    entry_params: tuple[tuple[str, str], ...]  # (param, _interp template) in route order
    entry_step_number: Optional[int]
    start_index: int
    entries: tuple[ReplayEntry, ...]
    tour_step_count: int
    layouts: tuple[tuple[str, str], ...] = ()  # (layout, media query) for layouts kept steps use
    test_id_attribute: str = "data-testid"
    end_index: Optional[int] = None  # first step after `tour: end`; None = the range runs to the last step
    start_in_place: bool = False  # `tour: start`: the tour must start on the page the setup reached

    def entry_for(self, step_number: int) -> ReplayEntry:
        for entry in self.entries:
            if entry.step_number == step_number:
                return entry
        raise KeyError(step_number)


@dataclass
class ScenarioInference:
    """The locale-independent result of inferring one scenario."""

    role: str
    entry_route: Optional[str]
    entry_params: tuple[tuple[str, str], ...]
    entry_step_index: Optional[int]
    start_index: int
    steps: list[TourStepPlan]
    entries: list[ReplayEntry] = field(default_factory=list)
    end_index: Optional[int] = None
    start_in_place: bool = False


@dataclass
class CompiledTour:
    """One tour, ready to serialize: inference + per-locale text."""

    id: str
    scenario_slug: str
    role: str
    entry_route: Optional[str]
    steps: list[TourStepPlan]
    locales: tuple[str, ...]  # emitted locales, in config order
    titles: dict[str, str]  # locale -> tour title
    step_texts: dict[str, list[tuple[Optional[str], Optional[str]]]]  # locale -> [(title, text)] per step
    guide_urls: dict[str, str]  # locale -> guideUrl (absent when no guide link)


@dataclass
class TourCompileResult:
    tours: list[CompiledTour]
    replay_plans: dict[str, ReplayPlan]
    diagnostics: list  # list[Diagnostic]

    @property
    def has_errors(self) -> bool:
        return any(d.severity == "error" for d in self.diagnostics)
