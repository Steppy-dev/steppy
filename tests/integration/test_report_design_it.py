"""Opt-in browser test of the run report's pages, opened from a file.

Skipped unless STEPPY_IT=1 (needs Playwright Chromium installed). Runs the real loop
against a page of solid-colour boxes served by http.server:

    steppy generate -> steppy run --screenshots -> steppy report

and reads what a browser makes of the site through ``file://``: the links with and
without JavaScript, the place of the click hotspot (in pixels), "Hide target", both
themes, no sideways scroll, the keyboard, and not one request beyond the files.

Run it:  STEPPY_IT=1 python -m pytest tests/integration/test_report_design_it.py -q
(PowerShell:  $env:STEPPY_IT='1'; python -m pytest tests/integration/... -q)
"""

import json
import math
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from textwrap import dedent

import pytest

from tests.integration.png_pixels import Png

pytestmark = pytest.mark.skipif(
    os.environ.get("STEPPY_IT") != "1",
    reason="browser integration test — set STEPPY_IT=1 to run",
)

#: The target, far below the fold: the capture is of a scrolled page.
TARGET = {"x": 60, "y": 1100, "width": 160, "height": 80}
GREEN = (0, 160, 0)
PAGE_HEIGHT = 1500

# "Pay" is missing in French on a phone, so one variant of four fails.
_HTML = f"""<!DOCTYPE html><html><body style="margin:0;background:#fff;height:{PAGE_HEIGHT}px;position:relative">
<button id="go" style="position:absolute;left:{TARGET["x"]}px;top:{TARGET["y"]}px;width:{TARGET["width"]}px;
height:{TARGET["height"]}px;border:0;padding:0;background:rgb{GREEN}"></button>
<form style="position:absolute;left:60px;top:40px"><input data-testid="first"><br><input data-testid="second"></form>
<p id="paid" style="position:absolute;top:200px">Paid</p>
<script>if (localStorage.lang === 'fr' && innerWidth < 600) document.getElementById('paid').remove();</script>
</body></html>"""

_CONFIG = """\
apps:
  editor:
    name: Demo
    url: "{url}"
    auth: {{mode: bypass}}
    i18n: {{localstorage: {{key: lang, values: {{en: en, fr: fr}}}}}}
locales: [en, fr]
default_locale: en
environment: staging
timing: {{step_timeout: 2000}}
screenshot:
  highlight_color: "rgb(0, 160, 0)"
  highlight_border: none
  layouts:
    desktop: {{width: 1280, height: 800}}
    mobile: {{width: 390, height: 844, device_scale_factor: 3}}
"""

_PAY = """\
---
title: Pay
auth: false
---

### 1. Open
- role: editor
- action: navigate
- target: /
- screenshot: editor

### 2. Press the button
- role: editor
- action: click
- selector: '#go'
- screenshot: editor

### 3. Press it again, on a phone
- role: editor
- action: click
- selector: '#go'
- layouts: [mobile]
- screenshot: editor

### 4. Fill in the form
- role: editor
- action: fill-form
- screenshot: editor
- fields:
  '[data-testid="first"]': "Ana"
  '[data-testid="second"]': "Reyes"

### 5. See it paid
- role: editor
- action: assert
- expect: visible(#paid)
- timeout: 800ms
- screenshot: editor
"""

_LOOK = """\
---
title: Look
auth: false
---

### 1. Open
- role: editor
- action: navigate
- target: /
- screenshot: editor
"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        body = _HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence per-request stderr noise
        pass


def _steppy(project: Path, *args: str, code: int = 0) -> str:
    done = subprocess.run(
        [sys.executable, "-m", "steppy", *args],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=240,
    )
    assert done.returncode == code, done.stdout + done.stderr
    return done.stdout + done.stderr


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> Path:
    """One real run of two scenarios over two locales and two layouts, rendered with every capture."""
    project = tmp_path_factory.mktemp("project")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        (project / "config.yaml").write_text(_CONFIG.format(url=url), encoding="utf-8")
        (project / "scenarios").mkdir()
        (project / "scenarios" / "pay.md").write_text(dedent(_PAY), encoding="utf-8")
        (project / "scenarios" / "look.md").write_text(dedent(_LOOK), encoding="utf-8")
        _steppy(project, "generate")
        _steppy(project, "run", "--screenshots", "--locales", "en,fr", "--layouts", "desktop,mobile", code=1)
        _steppy(project, "report", "--screenshots", "all")
    finally:
        server.shutdown()
    return project / "output" / "report"


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def open_page(browser, site):
    contexts = []

    def _open(address: str, *, width=1280, height=900, root=None, **kw):
        context = browser.new_context(viewport={"width": width, "height": height}, **kw)
        contexts.append(context)
        page = context.new_page()
        page.asked, page.problems = [], []
        page.on("request", lambda r: page.asked.append(r.url))
        page.on("pageerror", lambda e: page.problems.append(str(e)))
        page.on("console", lambda m: page.problems.append(m.text) if m.type in ("error", "warning") else None)
        path, _, fragment = address.partition("#")
        page.goto(((root or site) / path).as_uri() + (f"#{fragment}" if fragment else ""))
        page.wait_for_timeout(200)
        return page

    yield _open
    for context in contexts:
        context.close()


def _in_view(page, element_id: str) -> bool:
    return page.evaluate(
        """id => { const r = document.getElementById(id).getBoundingClientRect();
                   return r.top >= -1 && r.top < innerHeight; }""",
        element_id,
    )


def _notice(page) -> str:
    notice = page.locator("[id='ui/link-notice']")
    return notice.inner_text().strip() if notice.is_visible() else ""


PAGES = ["index.html", "scenarios/pay.html", "scenarios/look.html"]
STEP = "pay/fr/mobile/step-4"


# ── no network ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", PAGES)
def test_a_page_asks_for_files_only_and_complains_of_nothing(open_page, site, path):
    page = open_page(path)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(300)
    root = site.as_uri()
    strangers = [url for url in page.asked if not url.startswith((root, "data:"))]
    assert strangers == []
    assert page.problems == []


def test_the_brand_faces_load_from_the_report(open_page):
    page = open_page("index.html")
    assert page.evaluate("document.fonts.ready.then(() => document.fonts.check('16px \"Bricolage Grotesque\"'))")
    loaded = page.evaluate("[...document.fonts].filter(f => f.status === 'loaded').map(f => f.family)")
    assert {f.strip('"') for f in loaded} == {"Bricolage Grotesque", "JetBrains Mono"}


# ── links ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("js", [True, False], ids=["js", "no-js"])
@pytest.mark.parametrize("fragment", ["pay/fr/mobile", "pay/en/desktop", STEP, "pay/en/mobile/step-1"])
def test_a_link_works_on_the_scenarios_page(open_page, fragment, js):
    page = open_page(f"scenarios/pay.html#{fragment}", java_script_enabled=js)
    target = page.locator(":target")
    assert target.get_attribute("id") == fragment
    box = target.bounding_box()
    assert box and 0 <= box["y"] < 900
    assert page.url.endswith(f"scenarios/pay.html#{fragment}")


@pytest.mark.parametrize("js", [True, False], ids=["js", "no-js"])
def test_the_scenarios_own_link_opens_its_first_failing_variant(open_page, js):
    page = open_page("scenarios/pay.html#pay", java_script_enabled=js)
    box = page.locator("[id='pay/fr/mobile']").bounding_box()
    assert box and 0 <= box["y"] < 200


@pytest.mark.parametrize("fragment", ["pay", "pay/fr/mobile", STEP, "look/en/desktop/step-1"])
def test_the_index_sends_a_link_to_the_scenarios_page(open_page, fragment):
    page = open_page(f"index.html#{fragment}")
    assert page.url.endswith(f"scenarios/{fragment.split('/')[0]}.html#{fragment}")
    assert page.locator(":target").get_attribute("id") == fragment
    assert _notice(page) == ""


def test_the_index_redirect_leaves_no_step_in_the_history(open_page, site):
    page = open_page("scenarios/look.html")
    page.goto((site / "index.html").as_uri() + f"#{STEP}")
    page.wait_for_timeout(200)
    assert page.url.endswith(f"pay.html#{STEP}")
    page.go_back()
    assert page.url.endswith("look.html")


def test_without_javascript_the_index_still_holds_the_variant_it_shows(open_page):
    page = open_page(f"index.html#{STEP}", java_script_enabled=False)
    assert page.locator(":target").get_attribute("id") == STEP


def test_an_encoded_link_is_the_same_link(open_page):
    page = open_page("index.html#%70ay/%66r/mobile/step-4")
    assert "scenarios/pay.html" in page.url
    assert _notice(page) == "" and _in_view(page, STEP)


def test_a_variant_the_run_does_not_have_opens_the_scenarios_first_failing_one(open_page):
    page = open_page("index.html#pay/nl/mobile")
    assert page.url.endswith("scenarios/pay.html#pay/nl/mobile")
    assert _notice(page) == (
        "This link names pay · nl · mobile, which this run does not have. Showing pay · fr · mobile instead."
    )
    assert _in_view(page, "pay/fr/mobile")


def test_a_step_the_variant_does_not_have_opens_the_variant(open_page):
    page = open_page("index.html#pay/en/desktop/step-3")  # step 3 runs on phones only
    assert _notice(page) == "This link names step 3 of pay · en · desktop, which this run does not have."
    assert _in_view(page, "pay/en/desktop")


def test_a_scenario_the_run_does_not_have_stays_on_the_index(open_page):
    page = open_page("index.html#gone/fr/mobile/step-2")
    assert "index.html" in page.url
    assert _notice(page) == (
        "This link names gone · fr · mobile, which this run does not have. Showing pay · fr · mobile instead."
    )


def test_a_dash_reads_as_no_locale_or_layout(open_page):
    page = open_page("index.html#gone/-/-/step-2")
    assert _notice(page).startswith("This link names gone, which")


@pytest.mark.parametrize(
    "fragment",
    ["a/b", "pay/fr", "pay//mobile", "pay/fr/mobile/step-0", "pay/fr/mobile/step-x", "a/b/c/d/e", "%zz", "/"],
)
def test_what_is_not_a_link_says_so(open_page, fragment):
    page = open_page(f"index.html#{fragment}")
    assert "index.html" in page.url
    assert _notice(page) == "This link names nothing this run has. Showing pay · fr · mobile instead."
    assert page.problems == []


@pytest.mark.parametrize("name", ["__proto__", "constructor", "toString", "hasOwnProperty"])
def test_a_scenario_is_never_a_property_of_every_object(open_page, name):
    page = open_page(f"index.html#{name}")
    assert "index.html" in page.url
    assert _notice(page).startswith(f"This link names {name}, which")


def test_another_scenarios_link_goes_by_the_index(open_page):
    page = open_page("scenarios/look.html#pay/en/mobile")
    assert page.url.endswith("scenarios/pay.html#pay/en/mobile")
    gone = open_page("scenarios/look.html#gone")
    assert gone.url.endswith("index.html#gone") and _notice(gone).startswith("This link names gone")


def test_a_cause_has_an_anchor_and_the_variants_point_at_it(open_page):
    page = open_page("scenarios/pay.html")
    page.locator("a", has_text="Cause ").first.click()
    page.wait_for_timeout(200)
    assert "index.html#cause/" in page.url
    assert page.locator(":target").get_attribute("class") == "steppy-cause"
    assert _notice(page) == ""


def test_a_changed_fragment_is_followed_and_the_notice_goes(open_page):
    page = open_page("scenarios/pay.html#pay/nl/mobile")
    assert _notice(page)
    page.evaluate(f"location.hash = '{STEP}'")
    page.wait_for_timeout(200)
    assert _notice(page) == "" and _in_view(page, STEP)


def test_the_copied_links_lead_where_they_say(open_page):
    page = open_page("index.html")
    hrefs = page.eval_on_selector_all(".steppy-variant .steppy-copy-link", "all => all.map(a => a.href)")
    assert [h.rsplit("/", 5)[-1] for h in hrefs[1:]] == ["step-1", "step-2", "step-3", "step-4", "step-5"]
    page.locator(".steppy-step .steppy-copy-link").nth(3).click()
    page.wait_for_timeout(200)
    assert page.url.endswith(f"scenarios/pay.html#{STEP}")


def test_odd_names_and_locales_still_have_links(browser, tmp_path, open_page):
    """A space, an accent, a percent sign, and no locale at all."""
    from steppy.contracts.bundle import BundleManifest, BundleRun, BundleTest
    from steppy.contracts.report import ReportSourceInfo
    from steppy.report.model import build_model
    from steppy.report.render import render_site

    run = BundleRun(run_id="r", created_at="2026-11-25T20:00:00Z", steppy_version="0.10.0")
    names = [("mon scénario", "fr-BE", "grand écran"), ("50% off", None, None), ("a&b", "en", None)]
    tests = [
        BundleTest(scenario=s, nodeid=f"n{i}", outcome="failed", locale=locale, layout=layout, failed_step=3)
        for i, (s, locale, layout) in enumerate(names)
    ]
    model = build_model(
        manifest=BundleManifest(steppy_bundle=1, run=run, tests=tests), source=ReportSourceInfo(kind="tree")
    )
    render_site(model, site_dir=tmp_path / "odd", title="Odd")
    for fragment in (
        "mon%20sc%C3%A9nario/fr-BE/grand%20%C3%A9cran/step-3",
        "50%25%20off/-/-/step-3",
        "a&b/en/-",
    ):
        page = open_page(f"index.html#{fragment}", root=tmp_path / "odd")
        assert page.locator(":target").get_attribute("id") == fragment, page.url
        assert page.problems == []
    # As somebody types it: the browser encodes what a URL needs.
    typed = open_page("index.html#mon scénario/fr-BE/grand écran", root=tmp_path / "odd")
    assert typed.locator(":target").get_attribute("id") == "mon%20sc%C3%A9nario/fr-BE/grand%20%C3%A9cran"


@pytest.mark.parametrize("width", [375, 800, 1280])
def test_a_run_with_many_layouts_scrolls_its_tables_never_the_page(tmp_path, open_page, width):
    from steppy.contracts.bundle import BundleManifest, BundleRun, BundleTest
    from steppy.contracts.report import ReportSourceInfo
    from steppy.report.model import build_model
    from steppy.report.render import render_site

    run = BundleRun(run_id="r", created_at="2026-11-25T20:00:00Z", steppy_version="0.10.0")
    tests = [
        BundleTest(
            scenario="a-long-scenario-name" * 4, nodeid=f"n{x}{y}", outcome="failed", locale=x, layout=f"layout-{y}"
        )
        for x in ("en", "fr-BE")
        for y in range(9)
    ]
    model = build_model(
        manifest=BundleManifest(steppy_bundle=1, run=run, tests=tests), source=ReportSourceInfo(kind="tree")
    )
    render_site(model, site_dir=tmp_path / "wide", title="Wide" * 40)
    for path in ("index.html", f"scenarios/{'a-long-scenario-name' * 4}.html"):
        page = open_page(path, root=tmp_path / "wide", width=width)
        page.evaluate("document.querySelectorAll('details').forEach(d => { d.open = true; })")
        assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
        # Every cell can be reached: none is cut off by its card.
        cell = page.locator(".steppy-cell").last
        cell.scroll_into_view_if_needed()
        box = cell.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1


# ── click targets ───────────────────────────────────────────────────────────

VARIANTS = [("desktop", 1280, 800), ("mobile", 390, 844)]


@pytest.mark.parametrize("layout,css_width,css_height", VARIANTS)
def test_the_outline_is_on_the_target(open_page, site, layout, css_width, css_height):
    """In boxes first, then in pixels: what the outline surrounds is the button's green."""
    step = f"pay/en/{layout}/step-2"
    sidecar = site.parent / "screenshots" / "pay" / "en" / layout / "step-02.json"
    target = json.loads(sidecar.read_text(encoding="utf-8"))["target"]["bbox"]
    assert target["y"] != TARGET["y"]  # the page was scrolled for the capture
    page = open_page(f"scenarios/pay.html#{step}", device_scale_factor=4)
    shot = page.locator(f"[id='{step}'] .steppy-shot")
    page.wait_for_function("e => e.complete && e.naturalWidth", arg=shot.locator("img").element_handle())
    image = shot.locator("img").bounding_box()
    mark = shot.locator(".steppy-mark-box")
    assert mark.count() == 1
    # The content box, without the 1 px outline around it.
    inside = mark.evaluate(
        "e => { const r = e.getBoundingClientRect(); return [r.left + 1, r.top + 1, r.width - 2, r.height - 2]; }"
    )
    factor = image["width"] / css_width
    assert abs(image["height"] / css_height - factor) < 0.01
    expected = [
        image["x"] + target["x"] * factor,
        image["y"] + target["y"] * factor,
        target["width"] * factor,
        target["height"] * factor,
    ]
    assert all(abs(a - b) < 0.6 for a, b in zip(inside, expected)), (inside, expected)

    # Without the outline, the box it surrounded is green to its edges, and white around it.
    # By script: Playwright's own click would scroll the page to the box.
    page.evaluate("document.querySelector('.steppy-hide-target input').click()")
    origin_x, origin_y = math.floor(image["x"]), math.floor(image["y"])
    clip = {"x": origin_x, "y": origin_y, "width": math.ceil(image["width"]), "height": math.ceil(image["height"])}
    png = Png(page.screenshot(clip=clip))

    def pixel(x, y):
        return png.pixel(round((x - origin_x) * 4), round((y - origin_y) * 4))

    left, top, width, height = inside
    for x, y in ((0.5, 0.5), (0.1, 0.5), (0.9, 0.5), (0.5, 0.2), (0.5, 0.8)):
        inner = pixel(left + width * x, top + height * y)
        assert all(abs(p - g) < 30 for p, g in zip(inner, GREEN)), (x, y, inner)
    for x, y in ((-2, height / 2), (width + 2, height / 2), (width / 2, -2), (width / 2, height + 2)):
        assert min(pixel(left + x, top + y)) > 240, (x, y)


def test_a_form_has_one_outline_per_field(open_page):
    page = open_page(f"scenarios/pay.html#{STEP}")
    assert page.locator(f"[id='{STEP}'] .steppy-mark-box").count() == 2


@pytest.mark.parametrize("path", ["index.html", "scenarios/pay.html"])
@pytest.mark.parametrize("js", [True, False], ids=["js", "no-js"])
def test_hide_target_hides_every_outline_and_needs_no_script(open_page, path, js):
    page = open_page(path, java_script_enabled=js)
    marks = page.locator(".steppy-mark-box")
    assert marks.first.is_visible()
    box = page.get_by_label("Hide target")
    box.focus()
    page.keyboard.press("Space")
    assert box.is_checked()
    assert not any(marks.nth(i).is_visible() for i in range(marks.count()))
    page.keyboard.press("Space")
    assert marks.first.is_visible()


def test_the_run_pages_thumbnails_carry_their_outline(open_page):
    page = open_page("index.html")
    assert page.locator("[id='pay/fr/mobile/step-2'] .steppy-mark-box").count() == 1


def test_a_screenshot_opens_in_a_new_tab(open_page):
    page = open_page(f"scenarios/pay.html#{STEP}")
    link = page.locator(f"[id='{STEP}'] .steppy-frame-link")
    assert link.get_attribute("target") == "_blank" and link.get_attribute("rel") == "noopener"
    with page.expect_popup() as popup:
        link.click()
    assert popup.value.url.endswith(".png")


# ── the table ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("js", [True, False], ids=["js", "no-js"])
def test_the_table_opens_on_what_does_not_pass(open_page, js):
    page = open_page("index.html", java_script_enabled=js)
    rows = page.locator(".steppy-row")
    assert [r.is_visible() for r in rows.all()] == [True, False]  # pay, then look
    page.get_by_text("Show all 2 scenarios").click()
    assert all(r.is_visible() for r in rows.all())
    assert page.get_by_label("All 2", exact=True).is_checked()


def test_a_row_opens_into_its_locales_and_a_cell_leads_to_its_variant(open_page):
    page = open_page("index.html")
    page.locator(".steppy-row > summary").first.click()
    page.get_by_role("link", name="pay, fr · mobile: failed").click()
    page.wait_for_timeout(200)
    assert page.url.endswith("scenarios/pay.html#pay/fr/mobile")


# ── themes, sizes, keyboard, motion ─────────────────────────────────────────

GROUNDS = {"light": "rgb(246, 248, 244)", "dark": "rgb(14, 35, 28)"}


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("width", [1280, 375])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_a_page_fits_and_takes_the_theme(open_page, path, width, theme):
    page = open_page(path, width=width, color_scheme=theme)
    page.evaluate("document.querySelectorAll('details').forEach(d => { d.open = true; })")
    page.evaluate("document.querySelectorAll('input[value=all]').forEach(r => r.click())")
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == GROUNDS[theme]


@pytest.mark.parametrize("stamped,system", [("dark", "light"), ("light", "dark")])
def test_a_stamped_theme_beats_the_systems(open_page, stamped, system):
    page = open_page("index.html", color_scheme=system)
    page.evaluate(f"document.documentElement.dataset.theme = '{stamped}'")
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == GROUNDS[stamped]


@pytest.mark.parametrize("path", ["index.html", "scenarios/pay.html"])
def test_every_control_is_reached_by_the_keyboard_with_a_visible_focus(open_page, path):
    page = open_page(path)
    wanted = page.evaluate(
        """() => [...document.querySelectorAll('a[href], summary, input, [tabindex="0"]')]
            .filter(e => e.checkVisibility() && !(e.type === 'radio' && !e.checked)).length"""
    )
    reached = set()
    for _ in range(wanted + 20):
        page.keyboard.press("Tab")
        state = page.evaluate(
            """() => { const e = document.activeElement;
                if (!e || e === document.body) return null;
                const ring = [e, e.closest('label')].filter(Boolean).map(x => getComputedStyle(x))
                    .some(s => s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 2);
                const all = [...document.querySelectorAll('*')];
                return { at: all.indexOf(e), ring, what: e.outerHTML.slice(0, 90) }; }"""
        )
        if state is None:
            break
        assert state["ring"], state["what"]
        reached.add(state["at"])
    assert len(reached) == wanted


def test_a_row_opens_with_the_keyboard(open_page):
    page = open_page("index.html")
    page.locator(".steppy-row > summary").first.focus()
    page.keyboard.press("Enter")
    assert page.locator(".steppy-row").first.get_attribute("open") is not None


def test_less_motion_means_none(open_page):
    page = open_page("index.html", reduced_motion="reduce")
    assert (
        page.evaluate(
            "parseFloat(getComputedStyle(document.querySelector('.steppy-icon--chevron')).transitionDuration)"
        )
        <= 0.001
    )
