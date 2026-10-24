"""One scenario → its tour steps and the replay's per-step dispositions.

Pure and locale-independent: the same scenario yields the same steps whatever
locales are configured; text is resolved afterwards (``text.py``).

Order of decisions:

1. **Role.** ``tour.role``, else the scenario's only role.
2. **Range.** With a step marked ``tour: start``: that step, whatever led
   there; the entry is ``tour.entry``, else the last navigate before it when
   nothing in between may change the page, else none (``entry-unknown``).
   Otherwise, with ``tour.entry``: just after the navigate step whose target
   matches it — plus the run of ``only-in: guide`` steps right after an
   ``only-in: test`` navigate, which is the same journey by clicking (the "guide
   twin"). Without: just after the first navigate following the last
   ``login``. Steps before the start point are *setup*: not in the tour, but
   the replay runs them (in test mode) to get there. A step marked
   ``tour: end`` is the last of the range; the replay runs the steps after it
   once the tour completed.
3. **Mode.** ``only-in: test`` steps are dropped from the tour range (the guide
   path runs instead); ``only-in: guide`` steps are kept; other roles' steps
   run in the replay only.
4. **Candidates** per action (``actions.infer_candidate``), then annotations.
5. **Gates.** A hidden gate becomes the ``waitFor`` of the next kept step —
   unless a skipped step in between may change the page
   (``actions.page_change``), which drops it: the element may be gone by then,
   and a step waiting for it would never show. ``gate-stale`` says so when a
   kept step loses its wait that way.
"""

from __future__ import annotations

from typing import Optional

from ..models import Scenario, Step
from ..testids import TargetExtraction
from ..tour_spec import MAX_STEPS, TARGETED_ADVANCE_KINDS
from .actions import Candidate, infer_candidate, natural_target, page_change, replayable
from .diagnostics import DiagSink
from .model import ReplayEntry, ReplayTourStep, ScenarioInference, TourStepPlan
from .routes import app_base, bind, matches, parse_entry_pattern, route_from_target, target_segments
from .settings import ToursSettings


def _annotated(step: Step) -> bool:
    return bool(step.tour_text or step.tour_advance or step.tour_placement or step.tour)


def resolve_role(scenario: Scenario, configured_roles: set[str], sink: DiagSink) -> Optional[str]:
    roles = sorted(scenario.get_roles())
    wanted = scenario.tour.role if scenario.tour else None
    if wanted:
        if wanted not in roles or wanted not in configured_roles:
            sink.add(
                "tour-role-unknown",
                f"tour.role {wanted!r} is not a role this scenario uses ({', '.join(roles) or 'none'})",
            )
            return None
        return wanted
    if len(roles) == 1:
        return roles[0]
    if not roles:
        sink.add("tour-no-role", "the scenario has no role, so there is no app to run a tour in")
        return None
    sink.add(
        "tour-role-required",
        f"the scenario uses several roles ({', '.join(roles)}); say whose app the tour runs in",
        hint="set `tour: {role: <role>}`",
    )
    return None


def infer_scenario(scenario: Scenario, settings: ToursSettings, sink: DiagSink) -> Optional[ScenarioInference]:
    """Infer one opted-in scenario; ``None`` when an error makes the tour meaningless."""
    role = resolve_role(scenario, set(settings.app_urls), sink)
    if role is None:
        return None
    steps = scenario.steps
    base = app_base(settings.app_urls.get(role))
    if base.unresolved:
        sink.add(
            "app-url-unresolved",
            f"apps.{role}.url is not an absolute URL at compile time; routes are read relative to '/'",
        )

    def in_role(step: Step) -> bool:
        return step.role in (role, None)

    navs = [
        i
        for i, s in enumerate(steps)
        if s.action == "navigate" and not s.reload and s.role == role and s.only_in != "guide" and s.target
    ]
    logins = [i for i, s in enumerate(steps) if s.action == "login" and in_role(s)]
    last_login = logins[-1] if logins else -1

    starts = [i for i, s in enumerate(steps) if _marked(s, "start")]
    ends = [i for i, s in enumerate(steps) if _marked(s, "end")]
    if not _range_markers_ok(steps, starts, ends, sink):
        return None
    marked = starts[0] if starts else None

    entry_route: Optional[str] = None
    entry_params: tuple[tuple[str, str], ...] = ()
    k: Optional[int] = None

    explicit = scenario.tour.entry if scenario.tour else None
    pattern = parse_entry_pattern(explicit) if explicit else None
    if pattern is not None and pattern.error:
        sink.add("entry-invalid", pattern.error)
        return None
    if marked is not None:
        # The start is where the author put it, whatever led there. The entry is
        # then only the route the overlay takes a user to; the replay starts in
        # place and checks that this route is where the steps before it landed.
        if pattern is not None:
            entry_route = pattern.route
        else:
            k = _navigate_still_open(steps, navs, marked, in_role, sink)
            if k is not None:
                inferred = _inferred_entry(steps[k], base, sink)
                if inferred is None:
                    return None
                entry_route = inferred.route
    elif pattern is not None:
        candidates = []
        for i in navs:
            segs = target_segments(steps[i].target, base)
            if segs is not None and matches(pattern.segments, segs):
                candidates.append((i, segs))
        if not candidates:
            seen = ", ".join(repr(steps[i].target) for i in navs) or "none"
            sink.add(
                "entry-no-match",
                f"tour.entry {explicit!r} matches no navigate step (navigations: {seen})",
                hint="the tour starts right after the navigate step that opens the entry route",
            )
            return None
        after = [c for c in candidates if c[0] > last_login]
        k, segs = (after or candidates)[0]
        entry_route = pattern.route
        entry_params = bind(pattern.segments, segs)
    else:
        after = [i for i in navs if i > last_login]
        k = (after or navs or [None])[0]
        if k is None:
            sink.add(
                "no-entry",
                "the scenario never navigates, so the tour has no entry route and the replay would start on a blank page",
                hint="add a navigate step, or set tours.replay: false",
            )
        else:
            result = _inferred_entry(steps[k], base, sink)
            if result is None:
                return None
            entry_route = result.route
            entry_params = tuple(
                (seg[1:], raw) for seg, raw in zip(result.segments, result.raw_segments) if seg.startswith(":")
            )

    if marked is not None:
        start = marked
    else:
        start = 0 if k is None else k + 1
        if k is not None and steps[k].only_in == "test":
            while start < len(steps) and steps[start].only_in == "guide":
                start += 1
    end = ends[0] + 1 if ends else len(steps)
    if ends and end <= start:
        where = f"step {_number(steps, start)}" if start < len(steps) else "after the last step"
        sink.add(
            "range-reversed",
            f"`tour: end` is on a step before the tour starts ({where})",
            step=steps[ends[0]],
            hint="move `tour: end` to a step after the start",
        )
        return None

    kept: list[TourStepPlan] = []
    # (entry index, list of plans) so replay tour_steps can be filled after ids are assigned
    entries: list[tuple[int, str, str, list[TourStepPlan], bool]] = []
    pending_gate: Optional[str] = None
    # (gate, the step that dropped it, why): reported only if a kept step would have waited for it.
    stale: Optional[tuple[str, Step, str]] = None
    attr = settings.test_id_attribute

    for i, step in enumerate(steps):
        if i < start:
            disposition = "drop" if step.only_in == "guide" else "setup"
            entries.append((i, disposition, "before-entry", [], False))
            if _annotated(step):
                sink.add(
                    "annotation-outside-tour",
                    "tour annotations on a step before the tour starts have no effect",
                    step=step,
                    hint="remove them, or start the tour earlier (`tour: start`, `tour.entry`)",
                )
            continue
        if i >= end:
            disposition = "drop" if step.only_in == "guide" else "after"
            entries.append((i, disposition, "after-end", [], False))
            if _annotated(step):
                sink.add(
                    "annotation-outside-tour",
                    "tour annotations on a step after the tour ends have no effect",
                    step=step,
                    hint="remove them, or move `tour: end` later",
                )
            continue
        if step.only_in == "test":
            entries.append((i, "drop", "only-in-test", [], False))
            if _annotated(step):
                sink.add("annotation-outside-tour", "tour annotations on an `only-in: test` step", step=step)
            continue
        if not in_role(step):
            entries.append((i, "run", "other-role", [], False))
            if _annotated(step):
                sink.add("annotation-outside-tour", f"tour annotations on another role's step ({step.role})", step=step)
            continue

        cand = _apply_annotations(step, infer_candidate(step, attr), settings, sink)
        if cand.kind == "gate":
            pending_gate, stale = cand.gate, None
            entries.append((i, "run", "gate", [], False))
        elif cand.kind == "kept":
            plans: list[TourStepPlan] = []
            for j, sub in enumerate(cand.subs):
                tid = sub.extraction.test_id if sub.extraction is not None and sub.extraction.ok else None
                if tid and sub.extraction.reduced_from:
                    sink.add(
                        "reduced-to-host",
                        f"'{sub.selector}' points at the host test-id {tid!r}",
                        step=step,
                    )
                wait_for = pending_gate if j == 0 and pending_gate and pending_gate != tid else None
                if j == 0 and stale is not None and stale[0] != tid:
                    gate, cause, why = stale
                    number = step.step_number or i + 1
                    sink.add(
                        "gate-stale",
                        f"{why}, so step {number} no longer waits for {gate!r} (an earlier check)",
                        step=cause,
                        hint=f"add a `wait-for` on a test-id after this step if step {number} should wait for the page",
                    )
                advance = cand.advance or "manual"
                plans.append(
                    TourStepPlan(
                        step_index=i,
                        step_number=step.step_number or i + 1,
                        step_title=step.step_title or "",
                        action=step.action,
                        target=tid,
                        source_selector=sub.selector if tid else None,
                        advance=advance,
                        wait_for=wait_for,
                        placement=step.tour_placement if step.tour_placement and tid else None,
                        layouts=tuple(step.layouts) if step.layouts else None,
                        field=sub.field,
                    )
                )
            if step.tour_placement and step.tour_placement != "auto" and not any(p.target for p in plans):
                sink.add("placement-ignored", "a centred step has no side to place the popover on", step=step)
            kept.extend(plans)
            pending_gate, stale = None, None
            entries.append((i, "tour", "tour-step", plans, False))
        else:
            entries.append((i, "run", cand.reason or step.action, [], cand.reloads_page))
            if cand.reason == "conditional":
                sink.add(
                    "conditional-skipped",
                    "guarded by `when:`/`optional:`, so it may not happen; a linear tour leaves it out",
                    step=step,
                )
            elif cand.reason == "gate-dropped":
                sink.add(
                    "gate-dropped",
                    "this check's selector is not a test-id, so the next tour step does not wait for it",
                    step=step,
                    hint="point the check at a test-id to make it a gate",
                )
            elif cand.reason != "tour-skip":
                sink.add("step-skipped", f"left out of the tour ({cand.reason or step.action})", step=step)
            why = page_change(step) if pending_gate else None
            if why:
                stale, pending_gate = (pending_gate, step, why), None
        for code, message, hint in cand.notes:
            sink.add(code, message, step=step, hint=hint)
        for gate in cand.trailing_gates:
            pending_gate, stale = gate, None

    if not kept:
        sink.add(
            "no-kept-steps",
            "the tour has no steps after the start point",
            hint="check where it starts and ends (`tour.entry`, `tour: start`, `tour: end`), or mark steps `tour: keep`",
        )
        return None
    if len(kept) > MAX_STEPS:
        sink.add("limit-exceeded", f"the tour has {len(kept)} steps; the player accepts at most {MAX_STEPS}")
        return None
    mapped = set(settings.layout_map)
    for plan in kept:
        if plan.layouts and not set(plan.layouts) <= mapped:
            sink.add(
                "layouts-unmapped",
                f"this step only runs on layouts {list(plan.layouts)}, which tours.layouts does not map to media queries",
                step=steps[plan.step_index],
                hint="add them under tours.layouts in config.yaml",
            )
    if sink.has_errors:
        return None

    from .ids import assign_step_ids

    assign_step_ids(kept)
    index_of = {id(p): n for n, p in enumerate(kept)}
    replay_entries = []
    for i, disposition, reason, plans, reloads in entries:
        tour_steps = tuple(
            ReplayTourStep(
                step_id=p.step_id,
                index=index_of[id(p)],
                target=p.target,
                wait_for=p.wait_for,
                advance=p.advance,
                layouts=p.layouts,
                field=p.field,
                source_selector=p.source_selector,
            )
            for p in plans
        )
        replay_entries.append(
            ReplayEntry(
                step_number=steps[i].step_number or i + 1,
                disposition=disposition,  # type: ignore[arg-type]
                reason=reason,
                tour_steps=tour_steps,
                reloads_page=reloads,
            )
        )
    return ScenarioInference(
        role=role,
        entry_route=entry_route,
        entry_params=entry_params,
        entry_step_index=k,
        start_index=start,
        steps=kept,
        entries=replay_entries,
        end_index=end if ends else None,
        start_in_place=marked is not None,
    )


def _marked(step: Step, mode: str) -> bool:
    return mode in (step.tour or ())


def _number(steps: list[Step], i: int) -> int:
    return steps[i].step_number or i + 1


def _range_markers_ok(steps: list[Step], starts: list[int], ends: list[int], sink: DiagSink) -> bool:
    """One ``tour: start`` and one ``tour: end`` at most, and no start on the way to a page."""
    ok = True
    for mode, marked in (("start", starts), ("end", ends)):
        if len(marked) > 1:
            numbers = ", ".join(str(_number(steps, i)) for i in marked)
            sink.add(
                "range-duplicate",
                f"`tour: {mode}` is on steps {numbers}; a tour has one {mode}",
                step=steps[marked[1]],
                hint=f"keep `tour: {mode}` on one step",
            )
            ok = False
    if starts and steps[starts[0]].action in ("navigate", "login"):
        sink.add(
            "start-on-navigation",
            f"`tour: start` on a `{steps[starts[0]].action}` step: a tour starts on a page, not on the way to it",
            step=steps[starts[0]],
            hint="put it on the step after it, or set tour.entry",
        )
        ok = False
    return ok


def _inferred_entry(step: Step, base, sink: DiagSink):
    """The route a navigate opens, as an entry pattern; ``None`` after ``entry-invalid``."""
    result = route_from_target(step.target, base)
    if result.error:
        sink.add("entry-invalid", result.error, step=step, hint="set tour.entry to the route pattern")
        return None
    if result.query_dropped:
        sink.add("entry-query-dropped", "the entry's query string is ignored by in-app tours", step=step)
    if result.id_like:
        sink.add(
            "entry-id-segment",
            f"the entry route {result.route!r} contains fixture-looking ids ({', '.join(result.id_like)})",
            step=step,
            hint="set tour.entry with a :param, e.g. /invoices/:invoiceId",
        )
    return result


def _navigate_still_open(steps: list[Step], navs: list[int], start: int, in_role, sink: DiagSink) -> Optional[int]:
    """The navigate whose page a ``tour: start`` is still on, or ``None`` (``entry-unknown``).

    That is the last navigate before the start, provided nothing the replay
    runs in between may change the page — the rule ``gate-stale`` uses. A step
    that only runs in guide mode, or in another role's app, does not count.
    """
    before = [i for i in navs if i < start]
    hint = f"set tour.entry to the route of the page step {_number(steps, start)} is on"
    if not before:
        sink.add(
            "entry-unknown",
            "no navigate opens the page the tour starts on, so the tour has no entry route: "
            "started on another page, it stays there",
            step=steps[start],
            hint=hint,
        )
        return None
    k = before[-1]
    for i in range(k + 1, start):
        if steps[i].only_in == "guide" or not in_role(steps[i]):
            continue
        why = page_change(steps[i])
        if why:
            sink.add(
                "entry-unknown",
                f"step {_number(steps, i)} runs between the navigate of step {_number(steps, k)} and the start, "
                f"and {why}, so the tour has no entry route: started on another page, it stays there",
                step=steps[start],
                hint=hint,
            )
            return None
    return k


def _apply_annotations(step: Step, cand: Candidate, settings: ToursSettings, sink: DiagSink) -> Candidate:
    """Fold ``tour`` / ``tour-advance`` / ``tour-placement`` / ``tour-text`` into the candidate."""
    attr = settings.test_id_attribute
    extra = bool(step.tour_text or step.tour_advance or step.tour_placement)

    if cand.kind == "skip" and cand.reason == "conditional":
        if _marked(step, "keep"):
            sink.add(
                "keep-not-allowed",
                "a step guarded by `when:` or `optional:` may not happen, and a linear tour cannot branch",
                step=step,
            )
        elif extra:
            sink.add("annotation-on-skipped-step", "tour annotations on a `when:`/`optional:` step", step=step)
        return cand

    if _marked(step, "skip"):
        if extra:
            sink.add("skip-and-annotations", "`tour: skip` together with other tour annotations", step=step)
        return Candidate("skip", reason="tour-skip", trailing_gates=cand.trailing_gates, reloads_page=cand.reloads_page)

    if _marked(step, "keep"):
        if cand.kind != "kept":
            ext, selector = natural_target(step, attr)
            tid_ok = ext is not None and ext.ok
            forced = Candidate(
                "kept",
                advance=step.tour_advance or "manual",
                family="info",
                centred_ok=True,
                trailing_gates=cand.trailing_gates,
            )
            forced.subs.append(_sub(ext, selector, tid_ok))
            cand = forced
        else:
            for sub in cand.subs:
                if sub.extraction is not None and not sub.extraction.ok:
                    sub.extraction = None
            cand.centred_ok = True
            if step.tour_advance:
                cand.advance = step.tour_advance
            elif all(sub.extraction is None for sub in cand.subs):
                cand.advance = "manual"
    else:
        if cand.kind != "kept":
            if extra:
                sink.add(
                    "annotation-on-skipped-step",
                    f"tour annotations on a step the tour leaves out ({cand.reason or step.action})",
                    step=step,
                    hint="add `tour: keep` to show it",
                )
            return cand
        for sub in cand.subs:
            if sub.extraction is not None and not sub.extraction.ok and not cand.centred_ok:
                sink.add(
                    "target-not-test-id",
                    f"'{sub.selector}' is not a test-id: {sub.extraction.explain()}",
                    step=step,
                )
        if step.tour_advance:
            cand.advance = step.tour_advance

    if step.tour_advance and not replayable(cand, step.tour_advance):
        sink.add(
            "advance-not-replayable",
            f"`tour-advance: {step.tour_advance}` cannot be satisfied by this step's own `{step.action}`, "
            "so the tour replay would wait forever",
            step=step,
        )
    has_target = any(sub.extraction is not None and sub.extraction.ok for sub in cand.subs)
    # A non-centred step without a target already got `target-not-test-id`.
    if cand.centred_ok and not has_target and cand.advance in TARGETED_ADVANCE_KINDS:
        sink.add(
            "advance-needs-target",
            f"advance '{cand.advance}' needs a target, and this step has none",
            step=step,
            hint="use `tour-advance: manual`",
        )
    # A manual advance is the fix `may-not-change` asks for, so the note is moot.
    if cand.advance == "manual":
        cand.notes = [note for note in cand.notes if note[0] != "may-not-change"]
    return cand


def _sub(ext: Optional[TargetExtraction], selector: Optional[str], ok: bool):
    from .actions import Sub

    return Sub(ext if ok else None, selector if ok else None)
