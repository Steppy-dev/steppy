"""How each scenario action becomes (or does not become) a tour step.

``ACTION_TOUR_KIND`` classifies **every** action in ``models.VALID_ACTIONS``;
``tests/test_tour_action_table.py`` fails when a new action is added without a
row here — a missing row would silently drop that step from every tour.

Kinds:

``click`` / ``change`` / ``input``  the user acts on one target; the tour points
                                    at it and advances on that kind of event
``fill-form``                       one ``input`` sub-step per field
``upload``                          a file input, usually hidden behind a trigger
``check``                           an assertion / wait: a hidden gate, or an info
                                    step when it carries text
``cursor``                          ``custom`` / ``call``: only a ``guide-cursor``
                                    says what the user does
``unguided``                        the user must act but there is no test-id to
                                    point at (confirm dialogs)
``navigation``                      changes the page; never part of a tour
``skip``                            setup, waits, hovers, key presses
"""

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from ..assertions import AssertionSyntaxError, parse_assertion
from ..models import Step, expect_checks
from ..testids import TargetExtraction, extract_test_id

ACTION_TOUR_KIND: dict[str, str] = {
    "click": "click",
    "expand": "click",
    "toggle": "change",
    "select": "change",
    "select-option": "change",
    "pick-date": "change",
    "pick-time": "change",
    "fill": "input",
    "fill-date": "input",
    "fill-form": "fill-form",
    "upload": "upload",
    "assert": "check",
    "wait-for": "check",
    "custom": "cursor",
    "call": "cursor",
    "confirm": "unguided",
    "navigate": "navigation",
    "login": "navigation",
    "hover": "skip",
    "scroll": "skip",
    "keyboard": "skip",
    "wait": "skip",
    "wait-for-text": "skip",
    "capture": "skip",
}

#: Assertion forms that imply "this element is on screen" — the only ones that
#: can gate a step. ``hidden``/``not-visible``/``stays-hidden``/``count 0`` say
#: the opposite; ``toast``/``row-contains``/``url-contains`` have no target.
POSITIVE_FORMS = frozenset({"visible", "text", "contains", "icontains", "not-empty"})

#: ``custom`` code that acts on the page: skipping it inside a tour leaves the
#: user with an action nobody told them to do.
INTERACTIVE_CODE_RE = re.compile(
    r"\.(?:click|dblclick|fill|type|press|press_sequentially|check|uncheck|select_option|set_input_files"
    r"|dispatch_event)\(|dispatchEvent\("
)

#: Actions that leave the page as they found it. A gate (a hidden check) still
#: holds after one of them runs unshown inside a tour; after any other — a
#: skipped click or fill, a confirm, a key press, a navigate — the element it
#: waited for may be gone. An action added later counts as page-changing until
#: it is listed here, which is the safe side: a dropped gate only lets the next
#: step show sooner, a stale one keeps it from ever showing.
PAGE_NEUTRAL_ACTIONS = frozenset({"assert", "wait-for", "wait", "wait-for-text", "hover", "scroll", "capture"})

#: Calls ``custom`` code can make and still leave the page alone. Code calling
#: anything else — ``click``, ``goto``, ``evaluate``, a project helper — may
#: change it. An allowlist on purpose: on the consumer corpora, a denylist
#: (``INTERACTIVE_CODE_RE`` plus the navigations) missed form values set
#: through ``evaluate`` and clicks inside helpers.
_READ_ONLY_CALLS = frozenset(
    (
        # Playwright: locating, reading, waiting, asserting; the ``to_*`` / ``not_to_*`` matchers and the
        # ``expect_*`` waiters (whose ``with`` block holds the call that acts) are matched by prefix
        "locator get_by_role get_by_text get_by_label get_by_placeholder get_by_alt_text get_by_title "
        "get_by_test_id frame_locator filter nth and_ or_ all count is_visible is_hidden is_enabled is_disabled "
        "is_checked is_editable text_content inner_text inner_html input_value get_attribute all_text_contents "
        "all_inner_texts bounding_box title wait_for wait_for_timeout wait_for_selector wait_for_load_state "
        "wait_for_url wait_for_function expect "
        # The page-neutral actions above, written as code
        "hover scroll_into_view_if_needed "
        # Python builtins, and the list / str / re / datetime / time calls code uses to compute values
        "print len str int float bool list dict set tuple range sorted min max any abs round enumerate zip "
        "isinstance globals insert append extend strip lower upper split join replace format startswith endswith "
        "removeprefix removesuffix isdigit get keys items values compile sub search match fullmatch findall group "
        "now today date datetime timedelta weekday strftime isoformat time monotonic sleep"
    ).split()
)
_READ_ONLY_PREFIXES = ("to_", "not_to_", "expect_")

_STRING_RE = re.compile(r"'''.*?'''|\"\"\".*?\"\"\"|'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"", re.S)
_COMMENT_RE = re.compile(r"#[^\n]*")
_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")

#: Advance kinds the scenario's own action can satisfy in the replay. A
#: ``tour-advance`` outside this set would make the replay wait forever.
_REPLAYABLE = {
    "click": frozenset({"click", "manual", "visible"}),
    "input": frozenset({"input", "change", "manual"}),
    "change": frozenset({"change", "click", "manual"}),
    "upload": frozenset({"change", "manual"}),
    "info": frozenset({"manual", "visible"}),
}


@dataclass
class Sub:
    """One target of a kept candidate (fill-form has several)."""

    extraction: Optional[TargetExtraction]
    selector: Optional[str]
    field: Optional[str] = None


@dataclass
class Candidate:
    kind: Literal["kept", "gate", "skip"]
    reason: str = ""
    advance: Optional[str] = None
    subs: list[Sub] = field(default_factory=list)
    #: A failed/absent target is acceptable (centred step) — info steps, text-only custom.
    centred_ok: bool = False
    gate: Optional[str] = None
    #: Which replayable-advance family the step belongs to (see ``_REPLAYABLE``).
    family: str = "info"
    reloads_page: bool = False
    #: Deferred notes: emitted only if the step is not explicitly ``tour: skip``-ped.
    notes: list[tuple[str, str, Optional[str]]] = field(default_factory=list)
    trailing_gates: list[str] = field(default_factory=list)


def has_text(step: Step) -> bool:
    for text in (step.tour_text, step.guide_text):
        if text is None:
            continue
        values = [getattr(text, n) for n in ("fr", "en", "nl", "de")] + list((text.model_extra or {}).values())
        if any(v is not None for v in values):
            return True
    return False


def _extract(step: Step, selector: Optional[str], attr: str, *, scoped: bool = True) -> TargetExtraction:
    if scoped:
        return extract_test_id(selector, attr, within=step.within, match=step.match)
    return extract_test_id(selector, attr)


def _checks(step: Step, attr: str, *, scoped: bool) -> list[tuple[Optional[str], Optional[str], bool]]:
    """``(test-id, selector, positive)`` per assertion of the step."""
    out = []
    for check in expect_checks(step.expect):
        try:
            parsed = parse_assertion(check)
        except AssertionSyntaxError:
            continue
        selector = parsed.get("selector")
        if selector is None:
            continue
        kind = parsed["type"]
        positive = kind in POSITIVE_FORMS
        if kind == "count":
            positive = _int(parsed.get("count")) >= 1
        elif kind == "count-cmp":
            n, cmp = _int(parsed.get("count")), parsed.get("comparator")
            positive = (cmp in (">=", "==") and n >= 1) or (cmp == ">" and n >= 0)
        ext = _extract(step, selector, attr, scoped=scoped)
        out.append((ext.test_id, selector, positive))
    return out


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def trailing_gates(step: Step, attr: str) -> list[str]:
    """Positive, extractable ``expect:`` checks riding on a non-assert action.

    Extracted **without** ``within``/``match``: the trailing expect uses a plain
    locator in the generated test, whatever scopes the action itself.
    """
    if step.action in ("assert", "wait-for"):
        return []
    return [tid for tid, _sel, positive in _checks(step, attr, scoped=False) if positive and tid]


def natural_target(step: Step, attr: str) -> tuple[Optional[TargetExtraction], Optional[str]]:
    """The element a step is "about", for ``tour: keep`` on a skipped step."""
    if step.selector:
        return _extract(step, step.selector, attr), step.selector
    if step.guide_cursor is not None:
        return extract_test_id(step.guide_cursor.target, attr), step.guide_cursor.target
    for tid, selector, _positive in _checks(step, attr, scoped=True):
        return (extract_test_id(selector, attr, within=step.within, match=step.match), selector)
    if step.fields:
        key = next(iter(step.fields))
        return extract_test_id(key, attr), key
    return None, None


def infer_candidate(step: Step, attr: str) -> Candidate:
    """What inference alone makes of ``step`` (annotations are applied later)."""
    action = step.action
    kind = ACTION_TOUR_KIND[action]
    gates = trailing_gates(step, attr)

    if step.when or step.optional:
        return Candidate("skip", reason="conditional", trailing_gates=gates)

    if kind in ("click", "change", "input"):
        cand = Candidate("kept", advance=kind, family=kind, trailing_gates=gates)
        cand.subs.append(Sub(_extract(step, step.selector, attr), step.selector))
        if action == "expand":
            cand.notes.append(
                (
                    "may-not-change",
                    "an `expand` step only clicks when the section is closed, so its click may never happen",
                    "use `tour-advance: manual`, or `tour: skip`",
                )
            )
        if action == "toggle" and step.state:
            cand.notes.append(
                (
                    "may-not-change",
                    "a `toggle` with `state:` does nothing when the switch is already in that state",
                    "use `tour-advance: manual`, or `tour: skip`",
                )
            )
        return cand

    if kind == "fill-form":
        if not step.fields:
            return Candidate("skip", reason="fill-form-empty", trailing_gates=gates)
        cand = Candidate("kept", advance="input", family="input", trailing_gates=gates)
        for key in step.fields:
            cand.subs.append(Sub(extract_test_id(key, attr), key, field=key))
        return cand

    if kind == "upload":
        if step.guide_cursor is not None:
            target = step.guide_cursor.target
            cand = Candidate("kept", advance="manual", family="upload", trailing_gates=gates)
            cand.subs.append(Sub(extract_test_id(target, attr), target))
            return cand
        ext = _extract(step, step.selector, attr)
        cand = Candidate("kept", advance="change", family="upload", trailing_gates=gates)
        cand.subs.append(Sub(ext, step.selector))
        if ext.ok and ext.reduced_from is None:
            cand.notes.append(
                (
                    "upload-target-may-be-hidden",
                    "the tour points at the file input itself, which apps usually hide behind a styled button",
                    "add `guide-cursor: {behavior: click, target: <the visible trigger>}`",
                )
            )
        return cand

    if kind == "check":
        if action == "wait-for":
            positive = step.state in (None, "visible")
            ext = _extract(step, step.selector, attr)
            checks = [(ext.test_id, step.selector, positive)]
        else:
            checks = _checks(step, attr, scoped=True)
        if has_text(step):
            cand = Candidate("kept", advance="manual", family="info", centred_ok=True)
            first = next(((tid, sel) for tid, sel, pos in checks if pos and tid), None)
            if first is not None:
                cand.subs.append(Sub(TargetExtraction(test_id=first[0]), first[1]))
                cand.gate = first[0]
            else:
                cand.subs.append(Sub(None, None))
            return cand
        usable = [tid for tid, _sel, pos in checks if pos and tid]
        if usable:
            return Candidate("gate", gate=usable[-1], reason="gate")
        dropped = any(pos and not tid for tid, _sel, pos in checks)
        return Candidate("skip", reason="gate-dropped" if dropped else "check")

    if kind == "cursor":
        cursor = step.guide_cursor
        if cursor is not None and cursor.behavior in ("click", "type"):
            advance = "click" if cursor.behavior == "click" else "input"
            cand = Candidate("kept", advance=advance, family=advance, trailing_gates=gates)
            cand.subs.append(Sub(extract_test_id(cursor.target, attr), cursor.target))
            return cand
        if cursor is not None:
            return Candidate("skip", reason="hover-cursor", trailing_gates=gates)
        if has_text(step):
            cand = Candidate("kept", advance="manual", family="info", centred_ok=True, trailing_gates=gates)
            cand.subs.append(Sub(None, None))
            cand.notes.append(
                (
                    "unguided-action",
                    "this step has text but no target, so the tour shows it as a centred note while the user "
                    "must still do whatever the code does",
                    "add `guide-cursor: {behavior: click, target: <test-id selector>}`",
                )
            )
            return cand
        cand = Candidate("skip", reason=action, trailing_gates=gates)
        if action == "custom" and step.code and INTERACTIVE_CODE_RE.search(step.code):
            cand.notes.append(
                (
                    "unguided-action",
                    "this custom code acts on the page, but the tour has nothing to show for it; "
                    "the next step's target may never appear",
                    "migrate it to `action: click` (with `synthetic: true` if needed) or add `guide-cursor`",
                )
            )
        return cand

    if kind == "unguided":
        cand = Candidate("skip", reason=action, trailing_gates=gates)
        cand.notes.append(
            (
                "unguided-action",
                "confirm dialogs have no test-id to point at, so the tour cannot guide this click",
                "say what to do in the previous step's `tour-text`, or give the dialog buttons test-ids "
                "and click them by test-id",
            )
        )
        return cand

    if kind == "navigation":
        cand = Candidate("skip", reason=action, reloads_page=True, trailing_gates=gates)
        if action == "navigate" and not step.reload:
            from .routes import ID_LIKE_RE

            target = step.target or ""
            id_like = any(ID_LIKE_RE.fullmatch(seg) for seg in target.split("?")[0].split("/") if seg)
            cand.notes.append(
                (
                    "navigate-in-tour",
                    f"the tour cannot take the user to {target!r}"
                    + (" — it contains fixture ids no real user has" if id_like else ""),
                    "start the tour later (tour.entry), or let the user get there by clicking",
                )
            )
        else:
            cand.notes.append(
                (
                    "reloads-page",
                    "this step reloads the page or logs in again inside the tour",
                    "the tour resumes from sessionStorage, but move it before the tour starts if you can",
                )
            )
        return cand

    # plain skips
    cand = Candidate("skip", reason=action, trailing_gates=gates)
    if action == "keyboard" and "escape" in (step.value or "").lower():
        cand.notes.append(
            (
                "escape-in-tour",
                "an Escape key press inside the tour may dismiss it",
                "the player ignores Escape inside dialogs and open dropdowns; check the replay",
            )
        )
    return cand


def page_change(step: Step) -> Optional[str]:
    """Why ``step`` may change the page when it runs unshown, or ``None`` if it cannot.

    Phrased as the start of the ``gate-stale`` message. String literals and
    comments are blanked before ``custom`` code is scanned, so a selector such
    as ``':has-text("Save")'`` is not read as a call.
    """
    action = step.action
    if action in PAGE_NEUTRAL_ACTIONS:
        return None
    if action == "call":
        return None if step.page is False else f"the helper `{step.fn}` gets the page and may change it"
    if action == "custom":
        code = _COMMENT_RE.sub("", _STRING_RE.sub("''", step.code or ""))
        for name in _CALL_RE.findall(code):
            if not (keyword.iskeyword(name) or name in _READ_ONLY_CALLS or name.startswith(_READ_ONLY_PREFIXES)):
                return f"this code calls `{name}()`, which may change the page"
        return None
    return f"this `{action}` may change the page"


def replayable(candidate: Candidate, advance: str) -> bool:
    return advance in _REPLAYABLE.get(candidate.family, _REPLAYABLE["info"])
