"""``ReplayPlan`` → what ``test_scenario.py.j2`` renders as ``test_<slug>__tour``.

The replay is the scenario followed the way a *user* follows its tour, inside
the scenario's own test file (every selection, parallel, lastfailed and JUnit
path is keyed on that file):

1. **Setup** — the steps before the tour's start point, in test mode
   (``only-in: guide`` steps dropped), e.g. login and the entry navigation.
2. **Start** — the tour, through the overlay's element API.
3. **Range** — each tour step: assert the popover shows that step anchored on
   its target → perform the scenario's *own* action for it (the same codegen
   branch the scenario test uses, captures removed) → make sure the tour moved
   on. Range steps that are not tour steps run exactly as in the scenario.
4. **Complete** — ``tour-complete`` fired and no ``tour-error`` did.
5. **After** — the steps past ``tour: end``, in test mode, once the tour
   completed (cleanup keeps the replay repeatable).

A start marked ``tour: start`` was reached by whatever the setup did, so the
replay starts the tour without params and checks that the overlay did not have
to navigate: ``tour.entry`` must describe the page the setup landed on.

Every action body comes from the ``step_body`` template macro, so a replay can
never drift from the test it mirrors. This module only decides *which* steps,
in *which* order, with *which* tour checks around them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from ..models import SteppyConfig, Scenario, Step
from .model import ReplayEntry, ReplayPlan, ReplayTourStep

#: Actions that act on whatever has focus: clicking the popover's Next button
#: before them would steal it, so the Next click is deferred past them.
FOCUS_DEPENDENT_ACTIONS = frozenset({"keyboard", "custom", "call"})

ItemKind = Literal["setup", "plain", "omitted", "start", "tour", "deferred-ensure", "complete", "after"]


class ReplayPlanError(ValueError):
    """The compiler handed over a plan that does not line up with the scenario (a bug)."""


@dataclass(frozen=True)
class ReplayCheck:
    step_id: str
    selector: Optional[str]
    advance: str
    ensure: Literal["inline", "deferred"] = "inline"


@dataclass
class ReplayItem:
    kind: ItemKind
    comment: str = ""
    step: Optional[Step] = None
    #: tour items: one (step copy, check) per sub-step (fill-form has several)
    parts: list[tuple[Step, ReplayCheck]] = field(default_factory=list)
    step_id: Optional[str] = None  # deferred-ensure


@dataclass
class ReplayRender:
    function_name: str
    role: str
    roles: list[str]
    auth: bool
    spec: dict
    params: list[tuple[str, str]]
    items: list[ReplayItem]


def _copy(step: Step, **update) -> Step:
    """No captures in a replay; ``only-in`` is resolved statically, layouts/when stay runtime guards."""
    return step.model_copy(update={"screenshot": None, "only_in": None, **update})


def _check(plan: ReplayPlan, scenario: Scenario) -> None:
    if len(plan.entries) != len(scenario.steps):
        raise ReplayPlanError(f"{scenario.slug}: {len(plan.entries)} replay entries for {len(scenario.steps)} steps")
    seen_range = False
    expected_index = 0
    end = _end(plan, scenario)
    for idx, (step, entry) in enumerate(zip(scenario.steps, plan.entries)):
        if entry.step_number != (step.step_number or idx + 1):
            raise ReplayPlanError(f"{scenario.slug}: entry {idx} is for step {entry.step_number}")
        in_setup = idx < plan.start_index
        if in_setup and seen_range:
            raise ReplayPlanError(f"{scenario.slug}: setup step {entry.step_number} after the tour started")
        seen_range = seen_range or not in_setup
        if (entry.disposition == "after") != (idx >= end and step.only_in != "guide"):
            raise ReplayPlanError(f"{scenario.slug}: step {entry.step_number} is misplaced around the tour's end")
        if entry.disposition == "tour":
            if in_setup or idx >= end or step.role not in (plan.role, None) or step.only_in == "test":
                raise ReplayPlanError(f"{scenario.slug}: step {entry.step_number} cannot be a tour step")
            fields = list(step.fields or {})
            for ts in entry.tour_steps:
                if ts.index != expected_index:
                    raise ReplayPlanError(f"{scenario.slug}: tour step {ts.step_id} is out of order")
                expected_index += 1
                if ts.field is not None and ts.field not in fields:
                    raise ReplayPlanError(f"{scenario.slug}: {ts.field!r} is not a field of step {entry.step_number}")
    if expected_index != plan.tour_step_count:
        raise ReplayPlanError(f"{scenario.slug}: {expected_index} tour steps, plan says {plan.tour_step_count}")


def _end(plan: ReplayPlan, scenario: Scenario) -> int:
    return len(scenario.steps) if plan.end_index is None else plan.end_index


def _parts(step: Step, entry: ReplayEntry) -> list[tuple[Step, ReplayCheck]]:
    def check(ts: ReplayTourStep) -> ReplayCheck:
        return ReplayCheck(ts.step_id, ts.source_selector, ts.advance)

    if step.action == "fill-form" and any(ts.field for ts in entry.tour_steps):
        parts = []
        last = len(entry.tour_steps) - 1
        for n, ts in enumerate(entry.tour_steps):
            only = {ts.field: step.fields[ts.field]}
            parts.append(
                (
                    _copy(
                        step,
                        fields=only,
                        wait_after=step.wait_after if n == last else None,
                        expect=step.expect if n == last else None,
                    ),
                    check(ts),
                )
            )
        return parts
    (ts,) = entry.tour_steps
    return [(_copy(step), check(ts))]


def build_replay(scenario: Scenario, plan: ReplayPlan, config: SteppyConfig) -> ReplayRender:
    _check(plan, scenario)
    steps = scenario.steps
    items: list[ReplayItem] = []
    after: list[ReplayItem] = []
    end = _end(plan, scenario)
    started = False
    pending: Optional[tuple[str, Step]] = None  # deferred Next click: (step id, step for its guard)

    def flush_pending():
        nonlocal pending
        if pending is not None:
            step_id, guard_step = pending
            items.append(ReplayItem("deferred-ensure", step=guard_step, step_id=step_id))
            pending = None

    for idx, (step, entry) in enumerate(zip(steps, plan.entries)):
        title = step.step_title or ""
        if idx < plan.start_index:
            if entry.disposition == "drop":
                items.append(ReplayItem("omitted", comment=f"Step {entry.step_number}: {title} — guide-only, skipped"))
            else:
                items.append(
                    ReplayItem("setup", comment=f"Step {entry.step_number}: {title} — setup", step=_copy(step))
                )
            continue
        if idx >= end:
            if entry.disposition == "drop":
                after.append(ReplayItem("omitted", comment=f"Step {entry.step_number}: {title} — guide-only, skipped"))
            else:
                after.append(
                    ReplayItem("after", comment=f"Step {entry.step_number}: {title} — after the tour", step=_copy(step))
                )
            continue
        if not started:
            route = plan.entry_route or "the current page"
            where = "in place, " if plan.start_in_place else ""
            items.append(ReplayItem("start", comment=f"Tour '{plan.tour_id}' starts here ({where}entry {route})"))
            started = True
        if entry.disposition == "drop":
            items.append(
                ReplayItem("omitted", comment=f"Step {entry.step_number}: {title} — test-only, not in the tour")
            )
            continue
        if entry.disposition != "tour":
            items.append(ReplayItem("plain", comment=f"Step {entry.step_number}: {title}", step=_copy(step)))
            continue
        flush_pending()
        parts = _parts(step, entry)
        last_step, last_check = parts[-1]
        if last_check.advance in ("input", "manual") and _focus_dependent_follows(steps, plan.entries, idx):
            parts[-1] = (
                last_step,
                ReplayCheck(last_check.step_id, last_check.selector, last_check.advance, "deferred"),
            )
            pending = (last_check.step_id, _copy(step))
        ids = ", ".join(f"'{c.step_id}'" for _s, c in parts)
        items.append(
            ReplayItem(
                "tour",
                comment=f"Step {entry.step_number}: {title} — tour step {ids} ({parts[0][1].advance})",
                step=_copy(step),
                parts=parts,
            )
        )
    if not started:
        items.append(ReplayItem("start", comment=f"Tour '{plan.tour_id}' starts here"))
    flush_pending()
    items.append(ReplayItem("complete", comment=f"Tour '{plan.tour_id}' must complete"))
    items.extend(after)

    from ..generator import SteppyGenerator

    timeout = SteppyGenerator._resolve_timeout(None, scenario, config)
    tour_steps = [ts for e in plan.entries for ts in e.tour_steps]
    spec = {
        "id": plan.tour_id,
        "role": plan.role,
        "default_locale": plan.default_locale,
        "attribute": plan.test_id_attribute,
        "timeout_ms": int(timeout),
        "entry": _entry_spec(plan),
        "layouts": dict(plan.layouts),
        "steps": [
            {
                "id": ts.step_id,
                "target": ts.target,
                "wait_for": ts.wait_for,
                "advance": ts.advance,
                "layouts": list(ts.layouts) if ts.layouts else None,
                "scenario_step": e.step_number,
                "action": steps[i].action,
                "title": steps[i].step_title or "",
                "field": ts.field,
                "selector": ts.source_selector,
            }
            for i, e in enumerate(plan.entries)
            for ts in e.tour_steps
        ],
    }
    assert len(spec["steps"]) == len(tour_steps)
    slug_ = scenario.slug.replace("-", "_")
    return ReplayRender(
        function_name=f"test_{slug_}__tour",
        role=plan.role,
        roles=sorted(scenario.get_roles()),
        auth=scenario.auth,
        spec=spec,
        params=list(plan.entry_params),
        items=items,
    )


def _entry_spec(plan: ReplayPlan) -> Optional[dict]:
    if not plan.entry_route:
        return None
    spec: dict = {"route": plan.entry_route, "params": [list(p) for p in plan.entry_params]}
    if plan.start_in_place:
        spec["in_place"] = True
    return spec


def _focus_dependent_follows(steps: list[Step], entries: tuple[ReplayEntry, ...], idx: int) -> bool:
    for step, entry in zip(steps[idx + 1 :], entries[idx + 1 :]):
        if entry.disposition == "tour":
            return False
        if entry.disposition == "run" and step.action in FOCUS_DEPENDENT_ACTIONS:
            return True
    return False
