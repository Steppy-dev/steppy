"""The generated replay's runtime helpers (``templates/_steppy_tours.py``).

Driven against a fake page: the helper only ever reads the overlay through
``page.evaluate`` snapshots, the exposed event binding and a few locators, so a
scripted stand-in exercises every branch without a browser. A fake clock makes
the timeouts instant. The real overlay is covered by the opt-in browser suite.
"""

import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

HELPER = Path(__file__).resolve().parents[1] / "templates" / "_steppy_tours.py"


@pytest.fixture
def mod(monkeypatch):
    spec = importlib.util.spec_from_file_location("_steppy_tours_under_test", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    clock = SimpleNamespace(t=0.0)
    monkeypatch.setattr(module, "_now", lambda: clock.t)
    module._clock = clock
    return module


TARGET_BOX = {"x": 100, "y": 100, "width": 80, "height": 30}
SPOTLIGHT = {"x": 96, "y": 96, "w": 88, "h": 38}
BUBBLE_RECT = {"x": 100, "y": 150, "w": 300, "h": 120}


def _step(step_id, advance, n, action, title, target=None, layouts=None):
    return {
        "id": step_id,
        "target": target,
        "wait_for": None,
        "advance": advance,
        "layouts": layouts,
        "scenario_step": n,
        "action": action,
        "title": title,
        "field": None,
        "selector": f'[data-testid="{target}"]' if target else None,
    }


def _spec(**over):
    spec = {
        "id": "create-invoice",
        "role": "editor",
        "default_locale": "en",
        "attribute": "data-testid",
        "timeout_ms": 8000,
        "entry": {"route": "/invoices", "params": []},
        "layouts": {},
        "steps": [
            _step("new-invoice-btn", "click", 3, "click", "New invoice", target="new-invoice-btn"),
            _step("invoice-title", "input", 4, "fill", "Name it", target="invoice-title"),
            _step("done", "manual", 7, "assert", "Done"),
        ],
    }
    spec.update(over)
    return spec


class FakeContext:
    def __init__(self):
        self.bindings = {}
        self.init_scripts = []

    def expose_binding(self, name, callback):
        self.bindings[name] = callback

    def add_init_script(self, script):
        self.init_scripts.append(script)


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    def filter(self, visible=None):
        return self

    def bounding_box(self, timeout=None):
        return self.page.boxes.get(self.selector)

    def count(self):
        return 1 if self.selector in self.page.buttons else 0

    def is_disabled(self):
        return self.page.buttons[self.selector]["disabled"]

    def click(self):
        self.page.clicks.append(self.selector)
        self.page.buttons[self.selector]["on_click"]()

    def evaluate(self, script):
        assert ":hover" in script
        return self.selector in self.page.hovered

    def hover(self, timeout=None):
        if self.page.hover_error:
            raise RuntimeError(self.page.hover_error)
        self.page.hovered.add(self.selector)


class FakePage:
    """The overlay as the helper sees it: a snapshot dict plus scripted reactions."""

    def __init__(self, mod, *, viewport=(1280, 800)):
        self.mod = mod
        self.context = FakeContext()
        self.main_frame = object()
        self.viewport_size = {"width": viewport[0], "height": viewport[1]}
        self.handlers = {}
        self.snap = {
            "path": "/invoices",
            "defined": True,
            "host": True,
            "active": False,
            "current": None,
            "bubble": None,
            "spotlight": None,
            "pill": False,
        }
        self.media = {}
        self.boxes = {}
        self.buttons = {}
        self.clicks = []
        self.hovered = set()
        self.hover_error = None
        self.evaluated = []
        self.start_args = None
        self.start_result = {"ok": True}
        self.on_start = None
        self.scheduled = []

    # Playwright surface ------------------------------------------------------

    def on(self, name, callback):
        self.handlers[name] = callback

    def wait_for_timeout(self, ms):
        self.mod._clock.t += ms / 1000
        due = [item for item in self.scheduled if item[0] <= self.mod._clock.t]
        self.scheduled = [item for item in self.scheduled if item[0] > self.mod._clock.t]
        for _at, fn in due:
            fn()

    def evaluate(self, script, arg=None):
        if script is self.mod._SNAPSHOT_JS:
            return copy.deepcopy(self.snap)
        if script is self.mod._START_JS:
            self.start_args = arg
            if self.on_start:
                self.on_start()
            return self.start_result
        if "matchMedia" in script:
            return self.media[arg]
        self.evaluated.append(script)
        return None

    def locator(self, selector):
        return FakeLocator(self, selector)

    # Scripting the overlay ---------------------------------------------------

    def emit(self, _type, **detail):
        self.context.bindings[self.mod.TOUR_PROBE_BINDING](None, {"type": _type, "detail": detail, "path": "/x"})

    def show(self, step_id, *, index=0, total=3, state="shown", text="Do it.", target=None, spotlight=SPOTLIGHT):
        self.snap.update(
            active=True,
            current={"tourId": "create-invoice", "stepId": step_id, "index": index, "total": total},
            bubble={"stepId": step_id, "state": state, "rect": BUBBLE_RECT, "text": text, "next": None},
            spotlight=spotlight if target else None,
        )
        if target:
            self.boxes[f'[data-testid="{target}"]'] = TARGET_BOX
        self.emit("tour-step", stepId=step_id, index=index, total=total)

    def next_button(self, step_id, on_click, disabled=False):
        selector = f'{self.mod.TOUR_HOST_TAG} [part~="bubble"][{self.mod.ATTR_STEP_ID}="{step_id}"] [part~="next"]'
        self.buttons[selector] = {"disabled": disabled, "on_click": on_click}
        return selector

    def complete(self):
        self.snap.update(active=False, current=None, bubble=None, spotlight=None)
        self.emit("tour-complete")

    def later(self, seconds, fn):
        self.scheduled.append((self.mod._clock.t + seconds, fn))


def _request(params=None):
    callspec = SimpleNamespace(params=params if params is not None else {"editor_page": ("fr", "desktop")})
    return SimpleNamespace(node=SimpleNamespace(callspec=callspec))


def _desktop_runs(only_in, layouts):
    return not layouts or "desktop" in layouts


def _started(mod, spec=None, *, build_id="b-1"):
    page = FakePage(mod)
    tour = mod.TourProbe(page, _request(), spec or _spec(), step_runs=_desktop_runs)

    def start():
        page.emit("tour-start", tourId="create-invoice", buildId=build_id, source="bundled", resumed=False)
        page.show("new-invoice-btn", target="new-invoice-btn")

    page.on_start = start
    mod._tour_start(tour, {})
    return page, tour


def _first_line(excinfo) -> str:
    return str(excinfo.value).splitlines()[0]


# ── The probe ───────────────────────────────────────────────────────────────


def test_the_probe_collects_events_through_a_context_binding(mod):
    page = FakePage(mod)
    request = _request()
    tour = mod.TourProbe(page, request, _spec(), step_runs=_desktop_runs)
    assert mod.TOUR_PROBE_BINDING in page.context.bindings
    assert page.context.init_scripts == [mod._PROBE_JS]
    assert request.node._steppy_tour_probe is tour
    assert (tour.locale, tour.layout) == ("fr", "desktop")
    assert tour.timeout_ms == mod.DEFINE_TIMEOUT_MS  # never below the define budget
    page.emit("tour-step", stepId="x")
    assert tour.events == [{"type": "tour-step", "detail": {"stepId": "x"}, "path": "/x"}]


def test_an_unparametrized_test_has_no_locale_or_layout(mod):
    tour = mod.TourProbe(FakePage(mod), _request(params={}), _spec(), step_runs=_desktop_runs)
    assert (tour.locale, tour.layout) == (None, None)


def test_navigations_count_only_after_start(mod):
    page = FakePage(mod)
    tour = mod.TourProbe(page, _request(), _spec(), step_runs=_desktop_runs)
    page.handlers["framenavigated"](page.main_frame)
    assert tour.navigations == 0
    tour.start_mark = 0
    page.handlers["framenavigated"](object())  # a child frame
    page.handlers["framenavigated"](page.main_frame)
    assert tour.navigations == 1


# ── A whole tour ─────────────────────────────────────────────────────────────


def test_a_tour_followed_start_to_finish(mod):
    page, tour = _started(mod)
    # The kill switch is lifted for this context, for future documents and the current one.
    assert page.context.init_scripts[-1] == f"globalThis.{mod.TOURS_KILL_SWITCH_GLOBAL} = false;"
    assert any(mod.TOURS_KILL_SWITCH_GLOBAL in script for script in page.evaluated)
    assert page.start_args["id"] == "create-invoice" and page.start_args["locale"] == "fr"
    assert page.start_args["tag"] == mod.TOUR_HOST_TAG

    mod._tour_expect_step(tour, "new-invoice-btn", '[data-testid="new-invoice-btn"]')
    page.show("invoice-title", index=1, target="invoice-title")  # the scenario's click advanced it
    mod._tour_ensure_advanced(tour, "new-invoice-btn")

    mod._tour_expect_step(tour, "invoice-title", '[data-testid="invoice-title"]')
    next_title = page.next_button("invoice-title", lambda: page.show("done", index=2))
    mod._tour_ensure_advanced(tour, "invoice-title")  # input: grace, then Next
    assert page.clicks == [next_title]

    mod._tour_expect_step(tour, "done")
    next_done = page.next_button("done", page.complete)
    mod._tour_ensure_advanced(tour, "done")
    assert page.clicks == [next_title, next_done]
    mod._tour_expect_complete(tour)


def test_an_input_step_that_moves_on_by_itself_is_not_clicked(mod):
    page, tour = _started(mod)
    page.show("invoice-title", index=1, target="invoice-title")
    page.next_button("invoice-title", lambda: pytest.fail("Next must not be clicked"))
    page.later(0.2, lambda: page.show("done", index=2))  # a native change event advanced it
    mod._tour_ensure_advanced(tour, "invoice-title")
    assert page.clicks == []


def test_the_default_locale_is_used_without_a_locale_parametrization(mod):
    page = FakePage(mod)
    tour = mod.TourProbe(page, _request(params={}), _spec(), step_runs=_desktop_runs)
    page.on_start = lambda: page.emit("tour-start", buildId="b-1")
    mod._tour_start(tour, {"invoiceId": "1001"})
    assert page.start_args["locale"] == "en" and page.start_args["params"] == {"invoiceId": "1001"}


# ── Starting ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "error, problem, hint",
    [
        ("missing", "<steppy-tour-host> is not on the page", "mount it once in the app shell"),
        ("undefined", "<steppy-tour-host> was never defined", "import @steppy/player/tour"),
    ],
)
def test_a_host_that_is_missing_or_undefined(mod, error, problem, hint):
    page = FakePage(mod)
    page.start_result = {"error": error}
    tour = mod.TourProbe(page, _request(), _spec(), step_runs=_desktop_runs)
    with pytest.raises(mod.TourReplayError) as excinfo:
        mod._tour_start(tour, {})
    assert _first_line(excinfo) == f"[tour create-invoice]: {problem}"
    assert hint in str(excinfo.value)


@pytest.mark.parametrize(
    "reason, hint",
    [
        ("load-failed", "the app does not serve the tour files"),
        ("invalid-data", "regenerate them with steppy tours"),
        ("schema-unsupported", "older than the tour files"),
        ("entry-params", "route /invoices could not be filled from {}"),
    ],
)
def test_a_tour_that_does_not_start_says_why(mod, reason, hint):
    page = FakePage(mod)
    page.on_start = lambda: page.emit("tour-error", reason=reason, fatal=True)
    tour = mod.TourProbe(page, _request(), _spec(), step_runs=_desktop_runs)
    with pytest.raises(mod.TourReplayError) as excinfo:
        mod._tour_start(tour, {})
    assert _first_line(excinfo) == f"[tour create-invoice]: the tour did not start: {reason}"
    assert str(excinfo.value).splitlines()[1].startswith("  hint: ") and hint in str(excinfo.value)


def test_a_silent_host_times_out(mod):
    page = FakePage(mod)
    tour = mod.TourProbe(page, _request(), _spec(), step_runs=_desktop_runs)
    with pytest.raises(mod.TourReplayError, match="tour-start never fired"):
        mod._tour_start(tour, {})
    assert mod._clock.t >= mod.DEFINE_TIMEOUT_MS / 1000


def test_a_layout_mismatch_fails_before_starting(mod):
    page = FakePage(mod)
    page.media["(max-width: 767.98px)"] = True  # the viewport is "mobile" to the overlay...
    spec = _spec(layouts={"mobile": "(max-width: 767.98px)"})
    tour = mod.TourProbe(page, _request(), spec, step_runs=_desktop_runs)  # ...but desktop to the scenario
    with pytest.raises(mod.TourReplayError) as excinfo:
        mod._tour_start(tour, {})
    message = str(excinfo.value)
    assert "layout mismatch" in message and "'desktop'" in message and "1280x800" in message
    assert page.start_args is None


def test_the_strict_build_id_check_is_opt_in(mod, monkeypatch):
    _started(mod, build_id="b-1")  # no expectation: anything goes
    monkeypatch.setenv("STEPPY_TOUR_EXPECT_BUILD_ID", "b-1")
    _started(mod, build_id="b-1")
    monkeypatch.setenv("STEPPY_TOUR_EXPECT_BUILD_ID", "b-2")
    with pytest.raises(mod.TourReplayError, match="tours built for 'b-1', expected 'b-2'"):
        _started(mod, build_id="b-1")


# ── Each step ────────────────────────────────────────────────────────────────


def test_a_renamed_test_id_names_the_tour_step_and_test_id(mod):
    page, tour = _started(mod)
    page.emit("tour-error", reason="target-timeout", stepId="invoice-title", testId="invoice-title", fatal=False)
    with pytest.raises(mod.TourReplayError) as excinfo:
        mod._tour_expect_step(tour, "invoice-title", '[data-testid="invoice-title"]')
    lines = str(excinfo.value).splitlines()
    assert lines[0] == (
        "[tour create-invoice] step 'invoice-title' "
        '(scenario step 4 "Name it", fill, test-id "invoice-title"): '
        'target-timeout: the overlay never found [data-testid="invoice-title"]'
    )
    assert lines[1] == "  hint: the test-id was renamed or removed in the app, or the element appears too late"
    assert lines[-1].startswith("  last events: tour-start(") and lines[-1].endswith(
        "tour-error(target-timeout, invoice-title)"
    )


def test_a_missing_wait_for_gate_says_so(mod):
    page, tour = _started(mod)
    page.emit("tour-error", reason="target-timeout", stepId="done", testId="invoice-detail", kind="waitFor")
    with pytest.raises(mod.TourReplayError) as excinfo:
        mod._tour_expect_step(tour, "done")
    assert _first_line(excinfo).endswith('never found [data-testid="invoice-detail"] (the waitFor gate)')


def test_the_fallback_popover_is_a_target_timeout(mod):
    page, tour = _started(mod)
    page.show("new-invoice-btn", state="fallback", target="new-invoice-btn")
    with pytest.raises(mod.TourReplayError, match="'Can't find this element' fallback"):
        mod._tour_expect_step(tour, "new-invoice-btn", '[data-testid="new-invoice-btn"]')


def test_a_popover_without_text_fails(mod):
    page, tour = _started(mod)
    page.show("new-invoice-btn", text="", target="new-invoice-btn")
    with pytest.raises(mod.TourReplayError, match="the popover has no text"):
        mod._tour_expect_step(tour, "new-invoice-btn")


def test_a_spotlight_away_from_the_target_fails_with_both_rects(mod):
    page, tour = _started(mod)
    page.show("new-invoice-btn", target="new-invoice-btn", spotlight={"x": 600, "y": 400, "w": 80, "h": 30})
    with pytest.raises(mod.TourReplayError) as excinfo:
        mod._tour_expect_step(tour, "new-invoice-btn", '[data-testid="new-invoice-btn"]')
    assert "the spotlight" in _first_line(excinfo) and "is not on the target" in _first_line(excinfo)


def test_a_popover_covering_its_own_target_fails(mod):
    page, tour = _started(mod)
    page.show("new-invoice-btn", target="new-invoice-btn")
    page.snap["bubble"]["rect"] = {"x": 90, "y": 90, "w": 300, "h": 120}
    with pytest.raises(mod.TourReplayError, match="covers the centre of its own target"):
        mod._tour_expect_step(tour, "new-invoice-btn", '[data-testid="new-invoice-btn"]')


def test_an_invisible_target_fails(mod):
    page, tour = _started(mod)
    page.boxes.clear()
    with pytest.raises(mod.TourReplayError, match='the target \\[data-testid="new-invoice-btn"\\] is not visible'):
        mod._tour_expect_step(tour, "new-invoice-btn", '[data-testid="new-invoice-btn"]')


def test_a_popover_shown_late_is_waited_for(mod):
    page = FakePage(mod)
    tour = mod.TourProbe(page, _request(), _spec(), step_runs=_desktop_runs)
    page.on_start = lambda: page.emit("tour-start", buildId="b-1")
    mod._tour_start(tour, {})
    page.later(2.0, lambda: page.show("new-invoice-btn", target="new-invoice-btn"))
    mod._tour_expect_step(tour, "new-invoice-btn", '[data-testid="new-invoice-btn"]')
    assert mod._clock.t >= 2.0


def test_skipping_ahead_and_unknown_steps_fail(mod):
    page, tour = _started(mod)
    page.show("done", index=2)
    with pytest.raises(mod.TourReplayError, match="skipped ahead to 'done'"):
        mod._tour_expect_step(tour, "invoice-title")
    page.show("mystery")
    with pytest.raises(mod.TourReplayError, match="unknown step 'mystery'"):
        mod._tour_expect_step(tour, "invoice-title")


def test_a_tour_that_ends_early_fails_on_the_next_step(mod):
    page, tour = _started(mod)
    page.complete()
    with pytest.raises(mod.TourReplayError, match="completed before reaching this step"):
        mod._tour_expect_step(tour, "invoice-title")


# ── Visible steps ────────────────────────────────────────────────────────────


def _visible_spec():
    spec = _spec()
    spec["steps"].insert(2, _step("invoice-detail", "visible", 6, "assert", "Saved", target="invoice-detail"))
    return spec


def test_a_visible_step_whose_target_was_already_there_counts_as_reached(mod):
    page, tour = _started(mod, _visible_spec())
    # The overlay reached the step and, its target being on screen, moved on in
    # the same pass: no popover for it is ever drawn.
    page.show("invoice-detail", index=2, target="invoice-detail")
    page.show("done", index=3)
    mod._tour_expect_step(tour, "invoice-detail", '[data-testid="invoice-detail"]')
    mod._tour_ensure_advanced(tour, "invoice-detail")


def test_a_visible_step_shown_centred_needs_no_spotlight(mod):
    page, tour = _started(mod, _visible_spec())
    page.show("invoice-detail", index=2)  # centred: its target is not there yet
    mod._tour_expect_step(tour, "invoice-detail", '[data-testid="invoice-detail"]')
    page.later(0.5, lambda: page.show("done", index=3))  # the target appeared
    mod._tour_ensure_advanced(tour, "invoice-detail")


def test_a_visible_last_step_may_complete_the_tour_at_once(mod):
    spec = _spec()
    spec["steps"][-1].update(advance="visible", target="invoice-detail")
    page, tour = _started(mod, spec)
    page.show("done", index=2, target="invoice-detail")
    page.complete()
    mod._tour_expect_step(tour, "done", '[data-testid="invoice-detail"]')
    mod._tour_ensure_advanced(tour, "done")


def test_a_visible_step_that_is_never_reached_fails(mod):
    page, tour = _started(mod, _visible_spec())
    with pytest.raises(mod.TourReplayError, match="step 'invoice-detail' .*never reached this step"):
        mod._tour_expect_step(tour, "invoice-detail", '[data-testid="invoice-detail"]')
    page.show("done", index=3)
    with pytest.raises(mod.TourReplayError, match="skipped ahead to 'done'"):
        mod._tour_expect_step(tour, "invoice-detail")
    page.complete()
    with pytest.raises(mod.TourReplayError, match="completed before reaching this step"):
        mod._tour_expect_step(tour, "invoice-detail")


@pytest.mark.parametrize(
    "kind, detail, problem",
    [
        ("tour-dismiss", {"via": "escape"}, "the tour was dismissed (escape)"),
        ("tour-offpath", {"stepId": "new-invoice-btn"}, "the tour went off-path"),
        ("tour-error", {"reason": "invalid-data", "message": "bad"}, "tour-error invalid-data: bad"),
    ],
)
def test_terminal_events_fail_the_step_in_progress(mod, kind, detail, problem):
    page, tour = _started(mod)
    page.emit(kind, **detail)
    with pytest.raises(mod.TourReplayError) as excinfo:
        mod._tour_ensure_advanced(tour, "new-invoice-btn")
    assert problem in _first_line(excinfo)


# ── Advancing ────────────────────────────────────────────────────────────────


def test_a_disabled_next_means_the_field_stayed_empty(mod):
    page, tour = _started(mod)
    page.show("invoice-title", index=1, target="invoice-title")
    page.next_button("invoice-title", lambda: None, disabled=True)
    with pytest.raises(mod.TourReplayError, match="Next is disabled"):
        mod._tour_ensure_advanced(tour, "invoice-title")


def test_a_manual_step_without_next_fails(mod):
    page, tour = _started(mod)
    page.show("done", index=2)
    with pytest.raises(mod.TourReplayError, match="the popover has no Next button"):
        mod._tour_ensure_advanced(tour, "done")


@pytest.mark.parametrize(
    "advance, hint",
    [
        ("click", "the click did not reach the target"),
        ("change", "the overlay saw no value change on its target"),
    ],
)
def test_a_step_that_never_advances_explains_its_kind(mod, advance, hint):
    spec = _spec()
    spec["steps"][0]["advance"] = advance
    page, tour = _started(mod, spec)
    with pytest.raises(mod.TourReplayError) as excinfo:
        mod._tour_ensure_advanced(tour, "new-invoice-btn")
    assert f"did not advance after the step's action ({advance})" in _first_line(excinfo)
    assert hint in str(excinfo.value)


def test_the_last_step_may_complete_the_tour(mod):
    page, tour = _started(mod)
    page.complete()  # the final click ended the tour: advanced, not premature
    mod._tour_ensure_advanced(tour, "new-invoice-btn")


# ── Completing ───────────────────────────────────────────────────────────────


def test_completion_needs_every_applicable_step_shown(mod):
    spec = _spec()
    spec["steps"].insert(1, _step("burger", "click", 3, "click", "Menu", target="burger", layouts=["mobile"]))
    page, tour = _started(mod, spec)
    page.complete()
    # "burger" is a mobile-only step on a desktop run: not owed.
    with pytest.raises(mod.TourReplayError, match="step 'invoice-title' .*completed without showing this step"):
        mod._tour_expect_complete(tour)


def test_completion_that_never_comes_times_out(mod):
    page, tour = _started(mod)
    with pytest.raises(mod.TourReplayError, match="tour-complete never fired"):
        mod._tour_expect_complete(tour)


def test_an_error_without_a_step_reads_cleanly(mod):
    page = FakePage(mod)
    page.on_start = lambda: page.emit("tour-error", reason="load-failed", fatal=True)
    tour = mod.TourProbe(page, _request(), _spec(), step_runs=_desktop_runs)
    with pytest.raises(mod.TourReplayError) as excinfo:
        mod._tour_start(tour, {})
    assert str(excinfo.value).splitlines()[-1] == "  last events: tour-error(load-failed)"


def test_a_failure_message_quotes_selectors_safely(mod):
    assert mod._css_string('a"b\\c') == '"a\\"b\\\\c"'


# ── Hold zones ──────────────────────────────────────────────────────────────

ZONE = '[data-testid="side-nav"]'


def _held(mod):
    spec = _spec()
    spec["steps"][0]["hold"] = "side-nav"
    page, tour = _started(mod, spec)
    return page, tour


def test_a_hold_zone_clear_of_the_popover_is_hovered(mod):
    page, tour = _held(mod)
    page.boxes[ZONE] = {"x": 0, "y": 0, "width": 90, "height": 800}  # the bubble starts at x=100
    mod._tour_hold(tour, "new-invoice-btn")
    assert ZONE in page.hovered


def test_an_already_hovered_zone_is_left_alone(mod):
    page, tour = _held(mod)
    page.boxes[ZONE] = {"x": 0, "y": 0, "width": 90, "height": 800}
    page.hovered.add(ZONE)
    page.hover_error = "must not move the pointer"
    mod._tour_hold(tour, "new-invoice-btn")


def test_a_popover_over_the_hold_zone_fails_the_step(mod):
    page, tour = _held(mod)
    page.boxes[ZONE] = {"x": 0, "y": 0, "width": 240, "height": 800}
    with pytest.raises(mod.TourReplayError) as excinfo:
        mod._tour_hold(tour, "new-invoice-btn")
    assert 'the popover covers its hold zone "side-nav"' in str(excinfo.value)


def test_a_missing_hold_zone_fails_the_step(mod):
    page, tour = _held(mod)
    with pytest.raises(mod.TourReplayError) as excinfo:
        mod._tour_hold(tour, "new-invoice-btn")
    assert 'hold zone test-id "side-nav" is not on the page' in str(excinfo.value)


def test_a_hold_zone_the_pointer_cannot_reach_fails_the_step(mod):
    page, tour = _held(mod)
    page.boxes[ZONE] = {"x": 0, "y": 0, "width": 90, "height": 800}
    page.hover_error = "<steppy-tour-host> intercepts pointer events"
    with pytest.raises(mod.TourReplayError) as excinfo:
        mod._tour_hold(tour, "new-invoice-btn")
    assert "intercepts pointer events" in str(excinfo.value)
