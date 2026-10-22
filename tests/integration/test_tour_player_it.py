"""Opt-in browser integration test: the in-app tour overlay in Chromium (spec M2, M5).

Skipped unless ``STEPPY_TOUR_E2E=1`` (needs Playwright Chromium and the
built overlay, ``npm --prefix packages/player run build``). Runs the real
overlay in the demo app (``fixtures/tour-demo``) for what happy-dom cannot
show: hit-testing through the top layer, focus and the keyboard, the remote
loader against a second origin (CORS, SRI), and a guide site built by
``steppy build`` serving the tours.

Every step is also measured where the user sees it (``tour_geometry``): the
spotlight frames the target, the bubble stays on screen and off it, and a
click at the target lands on it — on a computer, and on a phone (390x844,
touch), where the app swaps its header links for a menu button and a drawer.

Run it:  STEPPY_TOUR_E2E=1 python -m pytest tests/integration/test_tour_player_it.py -q
"""

import importlib.util
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from tests.integration.tour_geometry import DESKTOP, PHONE, SHEETS, clear_of_dialogs, points_at

pytestmark = pytest.mark.skipif(
    os.environ.get("STEPPY_TOUR_E2E") != "1",
    reason="browser integration test — set STEPPY_TOUR_E2E=1 to run",
)

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "fixtures" / "tour-demo"
GOLDEN = ROOT / "contracts" / "fixtures" / "tours" / "valid"
EVENTS = ["tour-start", "tour-step", "tour-complete", "tour-dismiss", "tour-offpath", "tour-error"]
BUBBLE = 'steppy-tour-host [part~="bubble"]'

_spec = importlib.util.spec_from_file_location("tour_demo_serve", DEMO / "serve.py")
serve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve)

_RECORD = "window.__ev = [];" + "".join(
    f"addEventListener({json.dumps(name)}, (e) => window.__ev.push({{type: e.type, detail: e.detail}}), true);"
    for name in EVENTS
)


@pytest.fixture(scope="module")
def browser():
    bundle = Path(os.environ.get("STEPPY_TOUR_BUNDLE") or serve.DEFAULT_BUNDLE)
    if not bundle.is_file():
        pytest.fail(serve.BUILD_HINT)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def open_demo(browser):
    servers, contexts = [], []

    def _open(
        *, config=None, tours_dir=GOLDEN, connect_src=(), trusted_types=False, path="/invoices", phone=False, **ctx
    ):
        demo = serve.DemoServer(
            tours_dir=tours_dir, config=config, connect_src=connect_src, trusted_types=trusted_types
        )
        servers.append(demo)
        context = browser.new_context(base_url=demo.url, **{**(PHONE if phone else DESKTOP), **ctx})
        contexts.append(context)
        context.add_init_script(_RECORD)
        page = context.new_page()
        page.errors = []
        page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        page.goto(path)
        page.wait_for_selector(
            '[data-testid="invoices-footer"], [data-testid="invoice-detail"], [data-testid="customers-page"]',
            state="attached",
        )
        poll(page, "() => customElements.get('steppy-tour-host') !== undefined")
        page.demo = demo
        return page

    yield _open
    for context in contexts:
        context.close()
    for server in servers:
        server.stop()


def events(page, kind=None):
    evs = page.evaluate("window.__ev")
    return [e for e in evs if kind is None or e["type"] == kind]


def poll(page, script, arg=None, timeout=8000):
    """Wait until ``script(arg)`` is truthy. ``page.evaluate`` rather than
    ``wait_for_function``: the latter polls through ``eval``, which the demo's
    strict CSP (rightly) refuses."""
    deadline = time.monotonic() + timeout / 1000
    while True:
        value = page.evaluate(script, arg)
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {script} ({arg})")
        page.wait_for_timeout(50)


def wait_event(page, kind, timeout=8000, **match):
    poll(
        page,
        "([k, m]) => window.__ev.some(e => e.type === k && Object.entries(m).every(([a, b]) => e.detail[a] === b))",
        [kind, match],
        timeout,
    )


def at_step(page, step_id, timeout=8000):
    page.wait_for_selector(f'{BUBBLE}[data-step-id="{step_id}"][data-placed]', timeout=timeout)


def start(page, tour="create-invoice", **options):
    return page.evaluate("([t, o]) => document.querySelector('steppy-tour-host').start(t, o)", [tour, options])


def hit(page, test_id):
    """The test-id of what is under the centre of ``test_id`` (Playwright's click point)."""
    return page.evaluate(
        """(id) => { const t = document.querySelector(`[data-testid="${id}"]`); const r = t.getBoundingClientRect();
           const e = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
           return e && e.closest('[data-testid]') && e.closest('[data-testid]').getAttribute('data-testid'); }""",
        test_id,
    )


def progress(page):
    return page.locator(f'{BUBBLE} [part~="progress"]').text_content()


def follow_create_invoice(page, *, touch=False, measure=False, modes=None):
    """The create-invoice tour, followed with the mouse (or by touch); with
    ``measure``, each step is checked to point where the user acts."""
    act = page.tap if touch else page.click

    def at(step):
        at_step(page, step)
        if measure:
            points_at(page, step, modes=modes)

    at("new-invoice-btn")
    act('[data-testid="new-invoice-btn"]')
    at("invoice-title")
    page.fill('[data-testid="invoice-title"]', "Quarterly maintenance")
    act(f'{BUBBLE}[data-step-id="invoice-title"] [part~="next"]')
    at("customer-select")
    act('[data-testid="customer-select"]')
    option = page.locator(".p-select-overlay .p-select-option").filter(has_text="Acme Corp").first
    (option.tap if touch else option.click)(force=True)
    at("save-invoice-btn")
    act('[data-testid="save-invoice-btn"]')
    at("invoice-detail")


# ── Following a tour ──────────────────────────────────────────────────────────


def test_the_tour_by_mouse_never_blocks_the_app(open_demo):
    page = open_demo()
    page.click('[data-testid="start-tour-btn"]')
    for step in ["new-invoice-btn"]:
        at_step(page, step)
        assert hit(page, step) == step  # the layer and spotlight let clicks through
    follow_create_invoice(page, measure=True, modes=("anchored",))
    page.click(f'{BUBBLE}[data-step-id="invoice-detail"] [part~="next"]')
    wait_event(page, "tour-complete")
    assert [e["type"] for e in events(page)] == ["tour-start"] + ["tour-step"] * 5 + ["tour-complete"]
    assert [e["detail"]["stepId"] for e in events(page, "tour-step")] == [
        "new-invoice-btn",
        "invoice-title",
        "customer-select",
        "save-invoice-btn",
        "invoice-detail",
    ]
    assert page.evaluate("sessionStorage.getItem('demo:loads')") == "1"  # the save navigated client-side
    assert page.errors == []


def test_the_tour_by_keyboard_only(open_demo):
    page = open_demo()
    page.focus('[data-testid="start-tour-btn"]')
    page.keyboard.press("Enter")
    at_step(page, "new-invoice-btn")
    page.focus('[data-testid="new-invoice-btn"]')
    page.keyboard.press("Enter")
    at_step(page, "invoice-title")
    page.keyboard.type("Quarterly maintenance")  # the dialog focused its first field
    page.keyboard.press("Tab")  # a committed value advances an input step
    at_step(page, "customer-select")
    page.keyboard.press("Enter")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    at_step(page, "save-invoice-btn")
    page.focus('[data-testid="save-invoice-btn"]')
    page.keyboard.press("Enter")
    at_step(page, "invoice-detail")
    # A manual step takes focus: Enter completes the tour.
    poll(
        page,
        "() => document.querySelector('steppy-tour-host').shadowRoot.activeElement?.getAttribute('part')?.includes('next')",
    )
    # Keyboard activation within 300 ms of a step appearing is ignored on
    # purpose, so the keypress that advanced the previous step cannot also
    # press this one. A person is slower than that; the test waits like one.
    page.wait_for_timeout(350)
    page.keyboard.press("Enter")
    wait_event(page, "tour-complete")


def test_reduced_motion_disables_transitions(open_demo):
    page = open_demo(reduced_motion="reduce")
    start(page)
    at_step(page, "new-invoice-btn")
    duration = page.evaluate(
        "getComputedStyle(document.querySelector('steppy-tour-host').shadowRoot.querySelector('[part~=\"spotlight\"]')).transitionDuration"
    )
    assert set(duration.replace(" ", "").split(",")) == {"0s"}


def test_french_content_and_chrome(open_demo):
    page = open_demo()
    page.select_option('[data-testid="locale-select"]', "fr")
    page.click('[data-testid="start-tour-btn"]')
    at_step(page, "new-invoice-btn")
    assert page.locator(f'{BUBBLE} [part~="title"]').text_content() == "Nouvelle facture"
    assert page.locator(f'{BUBBLE} [part~="progress"]').text_content() == "Étape 1 sur 5"


# ── When the app does not match the tour ─────────────────────────────────────


def test_a_renamed_test_id_shows_the_fallback_and_skip_continues(open_demo):
    page = open_demo(config={"testIdOverrides": {"invoice-title": "invoice-name"}, "targetTimeout": 1000})
    start(page)
    at_step(page, "new-invoice-btn")
    page.click('[data-testid="new-invoice-btn"]')
    page.wait_for_selector(f'{BUBBLE}[data-state="fallback"][data-placed]')
    err = events(page, "tour-error")[0]["detail"]
    assert (err["reason"], err["testId"], err["kind"], err["fatal"]) == (
        "target-timeout",
        "invoice-title",
        "target",
        False,
    )
    page.click(f'{BUBBLE} [part~="skip"]')
    at_step(page, "customer-select")
    assert events(page, "tour-step")[-1]["detail"]["reason"] == "skip"


@pytest.mark.parametrize(
    "patch, reason",
    [
        (lambda t: t.update(schema=2), "schema-unsupported"),
        (lambda t: t["steps"][0].update(target='[data-testid="new-invoice-btn"]'), "invalid-data"),
    ],
)
def test_a_rejected_file_renders_nothing(open_demo, tmp_path, patch, reason):
    tours = tmp_path / "tours"
    shutil.copytree(GOLDEN, tours)
    data = json.loads((tours / "create-invoice.en.json").read_text(encoding="utf-8"))
    patch(data)
    (tours / "create-invoice.en.json").write_text(json.dumps(data), encoding="utf-8")
    page = open_demo(tours_dir=tours)
    assert start(page) is False
    assert events(page, "tour-error")[0]["detail"]["reason"] == reason
    assert page.locator(BUBBLE).count() == 0


# ── Remote mode (spec M5) ────────────────────────────────────────────────────


def _remote_config(remote) -> dict:
    return {
        "manifest": f"{remote.url}/tours/{{buildId}}/tours.manifest.json",
        "fallbackManifest": "/assets/tours/tours.manifest.json",
    }


@pytest.mark.parametrize(
    "mode, source", [("ok", "remote"), ("fail", "bundled"), ("tamper", "bundled"), ("nocors", "bundled")]
)
def test_remote_mode_falls_back_to_the_bundled_copy(open_demo, mode, source):
    remote = serve.RemoteServer(GOLDEN, allow_origin="", mode=mode, build_id="demo-0001")
    try:
        page = open_demo(config=_remote_config(remote), connect_src=(remote.url,))
        remote.allow_origin = page.demo.url  # known once the app runs; nothing is fetched before start()
        start(page)
        wait_event(page, "tour-start")
        assert events(page, "tour-start")[0]["detail"]["source"] == source
        at_step(page, "new-invoice-btn")
        if mode == "ok":
            assert "/tours/demo-0001/create-invoice.en.json" in remote.requests
    finally:
        remote.stop()


def test_a_built_guide_site_serves_the_tours(open_demo, tmp_path):
    """``steppy build`` (static) republishes the tree; the app reads it from there."""
    from steppy.build import BuildAssembler
    from steppy.models import SteppyConfig
    from tests.test_build_seo import _static_project

    build_config, root = _static_project(tmp_path)
    shutil.copytree(GOLDEN, root / "output" / "tours" / "demo-0001")
    config = SteppyConfig(
        apps={"editor": {"name": "E", "url": "http://localhost:3000", "auth": {"mode": "bypass"}}},
        locales=["fr", "nl"],
        default_locale="fr",
        tours={"enabled": True, "build_id": "demo-0001", "targets": [{"type": "guide"}]},
    )
    BuildAssembler(build_config, root, steppy_config=config).build_all()
    site_tours = root / "output" / "generated" / "tours"
    remote = serve.RemoteServer(site_tours, allow_origin="")
    try:
        page = open_demo(config=_remote_config(remote), connect_src=(remote.url,))
        remote.allow_origin = page.demo.url
        start(page)
        wait_event(page, "tour-start")
        assert events(page, "tour-start")[0]["detail"]["source"] == "remote"
    finally:
        remote.stop()


# ── Entry routes, resume, jumps ──────────────────────────────────────────────


def test_entry_routes_navigate_without_a_reload_and_need_their_params(open_demo):
    page = open_demo()
    assert start(page, "send-invoice") is False
    assert events(page, "tour-error")[0]["detail"]["missing"] == ["invoiceId"]
    page.click('[data-testid="start-send-tour-btn"]')  # the app passes {invoiceId: '1001'}
    at_step(page, "send-to")
    assert page.evaluate("location.pathname") == "/invoices/1001"
    assert events(page, "tour-start")[-1]["detail"]["entryNavigated"] is True
    assert page.evaluate("sessionStorage.getItem('demo:loads')") == "1"


def test_a_reload_resumes_the_tour(open_demo):
    page = open_demo()
    start(page)
    follow_create_invoice(page)
    page.reload()
    at_step(page, "invoice-detail")
    started = events(page, "tour-start")[-1]["detail"]
    assert started["resumed"] is True and started["entryNavigated"] is False
    assert page.evaluate("sessionStorage.getItem('demo:loads')") == "2"


def test_clicking_a_later_step_jumps_ahead(open_demo):
    page = open_demo()
    start(page)
    at_step(page, "new-invoice-btn")
    page.click('[data-testid="new-invoice-btn"]')
    at_step(page, "invoice-title")
    # The title stays empty until the tour has jumped: a filled one commits its
    # step when it loses focus, and that commit can land before the click below,
    # which then counts as a plain advance.
    page.click('[data-testid="customer-select"]')  # the next step's target: the tour moves there
    at_step(page, "customer-select")
    page.locator(".p-select-overlay .p-select-option").filter(has_text="Globex").first.click(force=True)
    page.fill('[data-testid="invoice-title"]', "Hosting")
    page.click('[data-testid="save-invoice-btn"]')
    at_step(page, "invoice-detail")
    reasons = {e["detail"]["stepId"]: e["detail"]["reason"] for e in events(page, "tour-step")}
    assert reasons["customer-select"] == "jump"


def test_a_lost_target_pauses_then_resumes_by_itself(open_demo):
    page = open_demo(config={"targetTimeout": 1000})
    start(page)
    at_step(page, "new-invoice-btn")
    page.click('[data-testid="new-invoice-btn"]')
    at_step(page, "invoice-title")
    page.click('[data-testid="cancel-invoice-btn"]')  # the dialog, and the step's target, go away
    page.wait_for_selector('steppy-tour-host [part~="pill"]')
    wait_event(page, "tour-offpath", stepId="invoice-title")
    page.click('[data-testid="new-invoice-btn"]')  # back in the dialog
    at_step(page, "invoice-title")
    assert events(page, "tour-step")[-1]["detail"]["reason"] == "resume"


# ── Escape, the kill switch and CSP ──────────────────────────────────────────


def test_escape_ends_the_tour_unless_the_app_uses_it(open_demo):
    page = open_demo()
    start(page)
    at_step(page, "new-invoice-btn")
    page.click('[data-testid="new-invoice-btn"]')
    at_step(page, "invoice-title")
    page.fill('[data-testid="invoice-title"]', "x")
    page.click(f'{BUBBLE}[data-step-id="invoice-title"] [part~="next"]')
    at_step(page, "customer-select")
    page.click('[data-testid="customer-select"]')
    page.keyboard.press("Escape")  # closes the dropdown (the app prevents default)
    page.wait_for_selector(".p-select-overlay", state="detached")
    assert events(page, "tour-dismiss") == []
    page.focus('[data-testid="invoice-title"]')
    page.keyboard.press("Escape")  # inside the dialog: the app's dialog closes, the tour stays
    assert events(page, "tour-dismiss") == []
    page.locator("body").focus()
    page.evaluate("document.activeElement.blur()")
    page.keyboard.press("Escape")
    wait_event(page, "tour-dismiss", via="escape")


def test_the_kill_switch_silences_auto_started_tours(open_demo, browser):
    page = open_demo(config={"autoStart": "create-invoice"})
    at_step(page, "new-invoice-btn")  # without the switch, the app's auto-start runs
    killed = open_demo(config={"autoStart": "create-invoice"}, path="/")
    killed.context.add_init_script("globalThis.__STEPPY_TOURS_DISABLED__ = true;")
    killed.reload()
    killed.wait_for_selector('[data-testid="invoices-footer"]', state="attached")
    killed.wait_for_timeout(500)
    assert killed.locator(BUBBLE).count() == 0 and events(killed) == []


def test_strict_csp_with_trusted_types(open_demo):
    page = open_demo(trusted_types=True)
    start(page)
    at_step(page, "new-invoice-btn")
    page.click('[data-testid="new-invoice-btn"]')
    at_step(page, "invoice-title")
    assert page.errors == []


# ── Language and links, in a real browser ────────────────────────────────────


def test_switching_language_mid_tour_reloads_the_tour_in_place(open_demo):
    page = open_demo()
    start(page)
    at_step(page, "new-invoice-btn")
    assert page.locator(f'{BUBBLE} [part~="title"]').text_content() == "New invoice"
    page.select_option('[data-testid="locale-select"]', "fr")  # the app sets host.locale and <html lang>
    poll(
        page,
        "(t) => document.querySelector('steppy-tour-host').shadowRoot.querySelector('[part~=title]')?.textContent === t",
        "Nouvelle facture",
    )
    layer = page.evaluate(
        "() => { const l = document.querySelector('steppy-tour-host').shadowRoot.querySelector('[part~=\"layer\"]');"
        " return [l.getAttribute('lang'), l.getAttribute('dir')]; }"
    )
    assert layer == ["fr", "ltr"]
    assert page.locator(BUBBLE).get_attribute("data-step-id") == "new-invoice-btn"
    assert events(page, "tour-error") == [] and page.errors == []


def test_a_link_that_resolves_off_site_stays_text(open_demo, tmp_path):
    tours = tmp_path / "tours"
    shutil.copytree(GOLDEN, tours)
    path = tours / "create-invoice.en.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["steps"][0]["text"] = "See [a](/.//evil.example/x) and [the list](/invoices)."
    path.write_text(json.dumps(data), encoding="utf-8")  # same-origin bundled files carry no SRI check
    page = open_demo(tours_dir=tours)
    start(page)
    at_step(page, "new-invoice-btn")
    hrefs = page.evaluate(
        "(s) => [...document.querySelector('steppy-tour-host').shadowRoot.querySelectorAll(s)]"
        ".map((a) => a.getAttribute('href'))",
        '[part~="body"] a',
    )
    assert hrefs == ["/invoices"]
    assert "[a](/.//evil.example/x)" in page.locator(f'{BUBBLE} [part~="body"]').text_content()


# ── Phones, the drawer, a long page and a confirmation ───────────────────────

CUSTOMERS_TOUR = [
    "menu-btn",
    "drawer-customers",
    "remove-inactive-btn",
    "confirm",
    "the-inactive-customers-are-removed",
]


def finish_removal(page, *, touch=False):
    """From the remove step: the confirmation, then the last two steps."""
    act = page.tap if touch else page.click
    act('[data-testid="remove-inactive-btn"]')
    at_step(page, "confirm")
    clear_of_dialogs(page)  # the step talks about the dialog: it must not cover it
    act(".p-confirmdialog-accept-button")
    page.wait_for_selector('[role="alert"]')
    act(f'{BUBBLE}[data-step-id="confirm"] [part~="next"]')
    at_step(page, "the-inactive-customers-are-removed")
    act(f'{BUBBLE}[data-step-id="the-inactive-customers-are-removed"] [part~="next"]')
    wait_event(page, "tour-complete")
    assert page.locator('[data-testid="customer-row"]').count() == 2


def test_create_invoice_by_touch_on_a_phone(open_demo):
    page = open_demo(phone=True)
    page.tap('[data-testid="start-tour-btn"]')
    follow_create_invoice(page, touch=True, measure=True, modes=SHEETS)
    page.tap(f'{BUBBLE}[data-step-id="invoice-detail"] [part~="next"]')
    wait_event(page, "tour-complete")
    assert events(page, "tour-error") == [] and page.errors == []


def test_on_a_phone_the_tour_goes_through_the_drawer(open_demo):
    page = open_demo(phone=True)
    page.tap('[data-testid="start-customers-tour-btn"]')
    points_at(page, "menu-btn", modes=SHEETS)
    assert progress(page) == "Step 1 of 5"  # the header-link step is the computer's
    page.tap('[data-testid="menu-btn"]')
    points_at(page, "drawer-customers", modes=SHEETS)  # once the drawer has slid in
    page.tap('[data-testid="drawer-customers"]')
    points_at(page, "remove-inactive-btn", modes=SHEETS)  # below the fold: scrolled to
    assert page.evaluate("scrollY") > 0
    finish_removal(page, touch=True)
    assert [e["detail"]["stepId"] for e in events(page, "tour-step")] == CUSTOMERS_TOUR
    assert page.evaluate("sessionStorage.getItem('demo:loads')") == "1"
    assert events(page, "tour-error") == [] and page.errors == []


def test_on_a_computer_the_same_tour_takes_the_header_link(open_demo):
    page = open_demo()
    page.click('[data-testid="start-customers-tour-btn"]')
    points_at(page, "nav-customers", modes=("anchored",))
    assert progress(page) == "Step 1 of 4"
    page.click('[data-testid="nav-customers"]')
    points_at(page, "remove-inactive-btn", modes=("anchored",))
    assert page.evaluate("scrollY") > 0
    finish_removal(page)
    assert [e["detail"]["stepId"] for e in events(page, "tour-step")] == ["nav-customers", *CUSTOMERS_TOUR[2:]]
    assert events(page, "tour-error") == [] and page.errors == []


def test_escape_closes_the_drawer_and_the_tour_waits_for_it(open_demo):
    page = open_demo(phone=True, config={"targetTimeout": 1000})
    page.tap('[data-testid="start-customers-tour-btn"]')
    at_step(page, "menu-btn")
    page.tap('[data-testid="menu-btn"]')
    points_at(page, "drawer-customers")
    page.keyboard.press("Escape")  # the drawer has focus and consumes it
    page.wait_for_selector('[data-testid="drawer"]', state="detached")
    page.wait_for_selector('steppy-tour-host [part~="pill"]')  # its target went away with it
    assert events(page, "tour-dismiss") == []
    page.tap('[data-testid="menu-btn"]')
    points_at(page, "drawer-customers", modes=SHEETS)
    assert events(page, "tour-step")[-1]["detail"]["reason"] == "resume"


def test_switching_language_on_a_phone_keeps_the_phone_steps(open_demo):
    page = open_demo(phone=True)
    page.tap('[data-testid="start-customers-tour-btn"]')
    at_step(page, "menu-btn")
    page.select_option('[data-testid="locale-select"]', "fr")
    poll(
        page,
        "(t) => document.querySelector('steppy-tour-host').shadowRoot.querySelector('[part~=progress]')?.textContent === t",
        "Étape 1 sur 5",
    )
    page.tap('[data-testid="menu-btn"]')
    points_at(page, "drawer-customers", modes=SHEETS)
    assert page.locator(f'{BUBBLE} [part~="title"]').text_content() == "Clients"
    assert progress(page) == "Étape 2 sur 5"


def test_a_focus_trap_that_pulls_focus_back_does_not_fight_the_bubble(open_demo):
    # Like CDK's cdkTrapFocus: focus leaving the dialog is pulled back. The
    # fallback bubble takes focus when it appears; the two must not trade it.
    page = open_demo(
        config={
            "focusTrap": "reclaim",
            "testIdOverrides": {"customer-select": "customer-picker"},
            "targetTimeout": 1000,
        }
    )
    start(page)
    at_step(page, "new-invoice-btn")
    page.click('[data-testid="new-invoice-btn"]')
    at_step(page, "invoice-title")
    page.fill('[data-testid="invoice-title"]', "Hosting")
    page.click(f'{BUBBLE}[data-step-id="invoice-title"] [part~="next"]')  # pressing it moves focus out
    page.wait_for_selector(f'{BUBBLE}[data-state="fallback"][data-placed]')
    page.evaluate("window.__moves = 0; document.addEventListener('focusin', () => window.__moves++, true)")
    page.wait_for_timeout(1000)
    assert page.evaluate("window.__moves") <= 1
    assert page.evaluate("document.activeElement.closest('[role=dialog]') !== null")  # the trap keeps it
    page.click(f'{BUBBLE} [part~="skip"]')
    at_step(page, "save-invoice-btn")
    assert events(page, "tour-dismiss") == []
