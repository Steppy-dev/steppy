"""Opt-in browser integration test: in-app tours in a real Angular + PrimeNG app.

Skipped unless ``STEPPY_TOUR_ANGULAR_E2E=1``. The demo
(``fixtures/tour-demo-angular/app``) is Angular 21 on zone.js with PrimeNG and
ngx-translate, and mounts the overlay through ``@steppy/angular``. It keeps
the plain demo's routes and test-ids, so the plain demo's Steppy project
(``fixtures/tour-demo/project``) drives it unchanged: the same scenarios, the
same tours, the same generated replays — against a dialog that is a real
``p-dialog`` and a dropdown that is a real ``p-select``.

What only this suite shows: the adapter's router bridge (an entry route is
reached through the Angular router, inside the zone, without a reload; a
refused navigation fails the tour at once), the zone hygiene (the overlay's
polls and observers drive no change detection), the locale following
ngx-translate mid-tour, and the replays passing on real PrimeNG markup — at
1280x800 and at 390x844, where the header links move into a ``p-drawer`` and
the customers tour ends on a ``p-confirmdialog``.

Build order (once)::

    npm --prefix packages/player ci && npm --prefix packages/player run build
    npm --prefix packages/angular ci && npm --prefix packages/angular run build
    npm --prefix fixtures/tour-demo-angular/app ci && npm --prefix fixtures/tour-demo-angular/app run build

Run it:  STEPPY_TOUR_ANGULAR_E2E=1 python -m pytest tests/integration/test_tour_angular_it.py -q
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.integration.tour_geometry import DESKTOP, PHONE, SHEETS, clear_of_dialogs, points_at

pytestmark = pytest.mark.skipif(
    os.environ.get("STEPPY_TOUR_ANGULAR_E2E") != "1",
    reason="browser integration test — set STEPPY_TOUR_ANGULAR_E2E=1 to run",
)

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "fixtures" / "tour-demo"
APP = ROOT / "fixtures" / "tour-demo-angular" / "app" / "dist" / "tour-demo-angular" / "browser"
GOLDEN = ROOT / "contracts" / "fixtures" / "tours" / "valid"
BUILD_HINT = (
    "the Angular demo is not built. Build the player, the adapter, then the demo:\n"
    "  npm --prefix packages/player ci && npm --prefix packages/player run build\n"
    "  npm --prefix packages/angular ci && npm --prefix packages/angular run build\n"
    "  npm --prefix fixtures/tour-demo-angular/app ci && npm --prefix fixtures/tour-demo-angular/app run build"
)
EVENTS = ["tour-start", "tour-step", "tour-complete", "tour-dismiss", "tour-offpath", "tour-error"]
BUBBLE = 'steppy-tour-host [part~="bubble"]'
# Angular and PrimeNG inject <style> elements; scripts stay 'self' only.
STYLE_SRC = ("'unsafe-inline'",)

_spec = importlib.util.spec_from_file_location("tour_demo_serve", DEMO / "serve.py")
serve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve)

_RECORD = "window.__ev = [];" + "".join(
    f"addEventListener({json.dumps(name)}, (e) => window.__ev.push({{type: e.type, detail: e.detail}}), true);"
    for name in EVENTS
)


def _require_build() -> None:
    if not (APP / "index.html").is_file():
        pytest.fail(BUILD_HINT)


def _demo(tours_dir: Path = GOLDEN, **config) -> "serve.DemoServer":
    return serve.DemoServer(APP, tours_dir=tours_dir, config=config or None, style_src=STYLE_SRC)


# ── The generated replays, against the Angular app ──────────────────────────


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    _require_build()
    from steppy.cli import app

    root = tmp_path_factory.mktemp("tour-demo-angular")
    shutil.copytree(DEMO / "project", root, dirs_exist_ok=True)
    cwd = os.getcwd()
    os.chdir(root)
    try:
        result = CliRunner().invoke(app, ["generate", "-o", "tests/e2e"])
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    return root


def _run(project: Path, url: str, *args: str) -> subprocess.CompletedProcess:
    environ = {k: v for k, v in os.environ.items() if not k.startswith("STEPPY_")}
    environ["TOUR_DEMO_URL"] = url
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/e2e", "-q", "-p", "no:cacheprovider", *args],
        cwd=project,
        env=environ,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _tours(project: Path) -> Path:
    return project / "app-assets" / "tours"


def test_the_scenarios_and_their_replays_pass(project):
    with _demo(_tours(project)) as demo:
        proc = _run(project, demo.url)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "12 passed" in proc.stdout


def test_a_renamed_test_id_fails_the_replay_naming_tour_step_and_test_id(project):
    with _demo(_tours(project), testIdOverrides={"invoice-title": "invoice-name"}, targetTimeout=1500) as demo:
        proc = _run(project, demo.url, "-k", "create_invoice__tour")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "[tour create-invoice] step 'invoice-title'" in out
    assert 'test-id "invoice-title"' in out and "target-timeout" in out


def test_the_kill_switch_keeps_an_auto_started_tour_off_ordinary_tests(project):
    with _demo(_tours(project), autoStart="create-invoice") as demo:
        proc = _run(project, demo.url, "-k", "create_invoice")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "4 passed" in proc.stdout


# ── The adapter in a browser ────────────────────────────────────────────────


@pytest.fixture(scope="module")
def browser():
    _require_build()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def open_app(browser):
    servers, contexts = [], []

    def _open(path="/invoices", phone=False, **config):
        demo = _demo(**config)
        servers.append(demo)
        context = browser.new_context(base_url=demo.url, **(PHONE if phone else DESKTOP))
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
        return page

    yield _open
    for context in contexts:
        context.close()
    for server in servers:
        server.stop()


def poll(page, script, arg=None, timeout=8000):
    """``page.evaluate`` polling: ``wait_for_function`` needs ``eval``, which the CSP refuses."""
    deadline = time.monotonic() + timeout / 1000
    while True:
        value = page.evaluate(script, arg)
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {script} ({arg})")
        page.wait_for_timeout(50)


def events(page, kind):
    return [e for e in page.evaluate("window.__ev") if e["type"] == kind]


def at_step(page, step_id, timeout=8000):
    page.wait_for_selector(f'{BUBBLE}[data-step-id="{step_id}"][data-placed]', timeout=timeout)


def state(page) -> str:
    return page.text_content('[data-testid="tour-state"]').strip()


def test_the_help_button_starts_the_tour_and_the_service_state_follows(open_app):
    page = open_app()
    page.click('[data-testid="start-tour-btn"]')
    at_step(page, "new-invoice-btn")
    poll(page, "() => document.querySelector('[data-testid=\"tour-state\"]').textContent.includes('new-invoice-btn')")
    assert state(page) == "active create-invoice new-invoice-btn 1/5"
    log = page.text_content('[data-testid="event-log"]')
    assert "tour-start" in log and "tour-step" in log


def test_a_tour_by_mouse_through_a_primeng_dialog_and_select(open_app):
    page = open_app()
    page.click('[data-testid="start-tour-btn"]')
    at_step(page, "new-invoice-btn")
    page.click('[data-testid="new-invoice-btn"]')
    at_step(page, "invoice-title")
    page.fill('[data-testid="invoice-title"]', "Quarterly maintenance")
    page.click(f'{BUBBLE}[data-step-id="invoice-title"] [part~="next"]')
    at_step(page, "customer-select")
    page.click('[data-testid="customer-select"]')
    page.wait_for_selector(".p-select-overlay", state="visible")  # past PrimeNG's opening animation
    page.locator(".p-select-overlay .p-select-option").filter(has_text="Acme Corp").first.click(force=True)
    at_step(page, "save-invoice-btn")
    page.click('[data-testid="save-invoice-btn"]')
    at_step(page, "invoice-detail")
    page.click(f'{BUBBLE}[data-step-id="invoice-detail"] [part~="next"]')
    poll(page, "() => window.__ev.some(e => e.type === 'tour-complete')")
    poll(page, "() => document.querySelector('[data-testid=\"tour-state\"]').textContent.trim() === 'idle'")
    assert events(page, "tour-error") == [] and events(page, "tour-dismiss") == []
    assert page.errors == []


def test_an_entry_route_is_reached_through_the_router_without_a_reload(open_app):
    # The send tour starts on /invoices/:invoiceId: from the list, the overlay
    # asks the adapter to navigate, the adapter runs the router inside the zone,
    # and the lazy detail page renders — no full page load.
    page = open_app()
    page.click('[data-testid="start-send-tour-btn"]')
    at_step(page, "send-to")
    assert page.evaluate("location.pathname") == "/invoices/1001"
    assert page.evaluate("sessionStorage.getItem('demo:loads')") == "1"
    assert events(page, "tour-start")[0]["detail"]["entryNavigated"] is True


def test_a_refused_navigation_fails_the_tour_at_once(open_app):
    page = open_app(blockDetail=True, targetTimeout=8000)
    t0 = time.monotonic()
    page.click('[data-testid="start-send-tour-btn"]')
    poll(page, "() => window.__ev.some(e => e.type === 'tour-error')", timeout=4000)
    assert time.monotonic() - t0 < 4
    error = events(page, "tour-error")[0]["detail"]
    assert error["reason"] == "entry-params" and error["fatal"] is True
    assert "did not navigate to /invoices/1001" in error["message"]
    assert page.evaluate("location.pathname") == "/invoices"


def test_switching_language_with_ngx_translate_mid_tour_reloads_the_tour(open_app):
    page = open_app()
    page.click('[data-testid="start-tour-btn"]')
    at_step(page, "new-invoice-btn")
    page.select_option('[data-testid="locale-select"]', "fr")
    poll(
        page,
        "() => document.querySelector('steppy-tour-host').shadowRoot.querySelector('[part~=\"title\"]')?.textContent.trim() === 'Nouvelle facture'",
    )
    assert (
        page.evaluate(
            "document.querySelector('steppy-tour-host').shadowRoot.querySelector('[part~=\"layer\"]').getAttribute('lang')"
        )
        == "fr"
    )
    assert page.evaluate("document.querySelector('steppy-tour-host').locale") == "fr"


def test_the_overlay_drives_no_change_detection_while_a_step_is_shown(open_app):
    page = open_app()
    page.click('[data-testid="start-tour-btn"]')
    at_step(page, "new-invoice-btn")
    page.wait_for_timeout(500)
    before = page.evaluate("window.__demoZoneTurns")
    page.wait_for_timeout(2000)  # eight of the overlay's 250 ms polls
    after = page.evaluate("window.__demoZoneTurns")
    assert after - before <= 2, f"{after - before} change-detection turns while idle on a step"


def test_a_reload_resumes_the_tour_and_the_service_sees_it(open_app):
    page = open_app()
    page.click('[data-testid="start-tour-btn"]')
    at_step(page, "new-invoice-btn")
    page.reload()
    at_step(page, "new-invoice-btn")
    poll(page, "() => window.__ev.some(e => e.type === 'tour-start' && e.detail.resumed)")
    poll(page, "() => document.querySelector('[data-testid=\"tour-state\"]').textContent.includes('new-invoice-btn')")
    assert page.errors == []


# ── A phone: the PrimeNG drawer, a long page and the ConfirmDialog ──────────


def _remove_inactive(page, *, touch=False):
    act = page.tap if touch else page.click
    act('[data-testid="remove-inactive-btn"]')
    at_step(page, "confirm")
    clear_of_dialogs(page)  # the step is about the confirmation: it must not hide it
    act(".p-confirmdialog-accept-button")
    page.wait_for_selector(".p-toast-message")
    act(f'{BUBBLE}[data-step-id="confirm"] [part~="next"]')
    at_step(page, "the-inactive-customers-are-removed")
    act(f'{BUBBLE}[data-step-id="the-inactive-customers-are-removed"] [part~="next"]')
    poll(page, "() => window.__ev.some(e => e.type === 'tour-complete')")
    assert page.locator('[data-testid="customer-row"]').count() == 2


def test_on_a_phone_the_tour_goes_through_the_primeng_drawer(open_app):
    page = open_app(phone=True)
    page.tap('[data-testid="start-customers-tour-btn"]')
    points_at(page, "menu-btn", modes=SHEETS)
    page.tap('[data-testid="menu-btn"]')
    points_at(page, "drawer-customers", modes=SHEETS)  # once the drawer has slid in
    page.tap('[data-testid="drawer-customers"]')
    points_at(page, "remove-inactive-btn", modes=SHEETS)  # below the fold: scrolled to
    _remove_inactive(page, touch=True)
    steps = [e["detail"]["stepId"] for e in events(page, "tour-step")]
    assert steps == [
        "menu-btn",
        "drawer-customers",
        "remove-inactive-btn",
        "confirm",
        "the-inactive-customers-are-removed",
    ]
    assert page.evaluate("sessionStorage.getItem('demo:loads')") == "1"
    assert events(page, "tour-error") == [] and page.errors == []


def test_on_a_computer_the_customers_tour_takes_the_header_link(open_app):
    page = open_app()
    page.click('[data-testid="start-customers-tour-btn"]')
    points_at(page, "nav-customers", modes=("anchored",))
    page.click('[data-testid="nav-customers"]')
    points_at(page, "remove-inactive-btn", modes=("anchored",))
    _remove_inactive(page)
    assert events(page, "tour-error") == [] and page.errors == []


def test_create_invoice_by_touch_on_a_phone(open_app):
    page = open_app(phone=True)
    page.tap('[data-testid="start-tour-btn"]')
    points_at(page, "new-invoice-btn", modes=SHEETS)
    page.tap('[data-testid="new-invoice-btn"]')
    points_at(page, "invoice-title", modes=SHEETS)
    page.fill('[data-testid="invoice-title"]', "Quarterly maintenance")
    page.tap(f'{BUBBLE}[data-step-id="invoice-title"] [part~="next"]')
    points_at(page, "customer-select", modes=SHEETS)
    page.tap('[data-testid="customer-select"]')
    page.wait_for_selector(".p-select-overlay", state="visible")
    page.locator(".p-select-overlay .p-select-option").filter(has_text="Acme Corp").first.tap(force=True)
    points_at(page, "save-invoice-btn", modes=SHEETS)
    page.tap('[data-testid="save-invoice-btn"]')
    points_at(page, "invoice-detail", modes=SHEETS)
    page.tap(f'{BUBBLE}[data-step-id="invoice-detail"] [part~="next"]')
    poll(page, "() => window.__ev.some(e => e.type === 'tour-complete')")
    assert events(page, "tour-error") == [] and page.errors == []


def test_escape_closes_the_primeng_drawer_and_the_tour_waits_for_it(open_app):
    page = open_app(phone=True, targetTimeout=1000)
    page.tap('[data-testid="start-customers-tour-btn"]')
    at_step(page, "menu-btn")
    page.tap('[data-testid="menu-btn"]')
    points_at(page, "drawer-customers")
    page.focus('[data-testid="drawer-customers"]')
    page.keyboard.press("Escape")  # PrimeNG closes the drawer and consumes the key
    page.wait_for_selector('[data-testid="drawer-customers"]', state="detached")
    page.wait_for_selector('steppy-tour-host [part~="pill"]')
    assert events(page, "tour-dismiss") == []
    page.tap('[data-testid="menu-btn"]')
    points_at(page, "drawer-customers", modes=SHEETS)
    assert events(page, "tour-step")[-1]["detail"]["reason"] == "resume"
