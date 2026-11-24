"""Opt-in browser integration test for screenshot masks (spec I7).

Skipped unless STEPPY_IT=1 (needs Playwright Chromium installed). Runs the real
loop against a page of solid-colour boxes served by http.server:

    steppy generate -> steppy run --screenshots

and reads the **pixels** of what landed on disk: the project's, the scenario's
and the step's masks in the step captures (fill-form frames included), all of
them in the failure captures of both role pages, a control box untouched, and
no image at all when the masked capture cannot be taken. An unmasked image an
earlier run left is gone too, whatever this run's outcome, from the folder and
from the report.

Run it:  STEPPY_IT=1 python -m pytest tests/integration/test_screenshot_mask_it.py -q
(PowerShell:  $env:STEPPY_IT='1'; python -m pytest tests/integration/... -q)
"""

import json
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

MASK_COLOR = (18, 52, 86)  # #123456
CONTROL = (0, 160, 0)

#: Every box of the page: its position and its colour when nothing covers it.
BOXES = {
    "project": ((20, 20, 120, 40), (255, 0, 0)),
    "iban": ((20, 100, 120, 40), (255, 128, 0)),
    "phone": ((20, 180, 120, 40), (255, 0, 255)),
    "control": ((20, 260, 120, 40), CONTROL),
    "far": ((20, 2400, 120, 40), (0, 0, 255)),
    "far-control": ((300, 2400, 120, 40), CONTROL),
}

_INDEX_HTML = (
    "<!DOCTYPE html><html><body style='margin:0;background:#fff'>"
    + "".join(
        f"<div data-testid='{name}' style='position:absolute;left:{x}px;top:{y}px;width:{w}px;height:{h}px;"
        f"background:rgb{colour}'></div>"
        for name, ((x, y, w, h), colour) in BOXES.items()
    )
    + "<form style='position:absolute;left:400px;top:20px'>"
    + "<input data-testid='first'><input data-testid='second'></form>"
    + "<button id='go' style='position:absolute;left:400px;top:120px'>Go</button>"
    + "<div style='position:absolute;top:3000px;width:1px;height:1px'></div>"
    + "</body></html>"
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        body = _INDEX_HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence per-request stderr noise
        pass


@pytest.fixture
def app_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture
def project(tmp_path: Path, app_url: str) -> Path:
    role = f'{{name: "Demo", url: "{app_url}", auth: {{mode: bypass}}}}'
    (tmp_path / "config.yaml").write_text(
        dedent(f"""\
            apps:
              editor: {role}
              reviewer: {role}
            screenshot:
              mask:
                - '[data-testid="project"]'
              mask_color: "#123456"
            timing: {{step_timeout: 2000}}
            """),
        encoding="utf-8",
    )
    (tmp_path / "scenarios").mkdir()
    return tmp_path


_FAILS = """
### 9. Never there
- role: editor
- action: assert
- expect: visible(#never)
- timeout: 500ms
"""

_OPEN = """
### 1. Open
- role: editor
- action: navigate
- target: /
- screenshot: editor
"""


def _run(project: Path, name: str, front: str, steps: str, outcome: str = "1 failed") -> str:
    """Write one scenario, generate it, run it; returns the run's output."""
    front = f"{front}\n" if front else ""
    (project / "scenarios" / f"{name}.md").write_text(
        f"---\ntitle: Masked\nauth: false\n{front}---\n{steps}", encoding="utf-8"
    )
    output = ""
    for args in (["generate"], ["run", "--screenshots", "--scenario", name], ["report"]):
        result = subprocess.run(
            [sys.executable, "-m", "steppy", *args],
            cwd=project,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=180,
        )
        output += result.stdout + result.stderr
        assert args[0] == "run" or result.returncode == 0, output
    assert outcome in output, output
    return output


def _captures(project: Path, name: str) -> dict[str, Png]:
    return {p.stem: Png(p) for p in sorted((project / "output" / "screenshots" / name).glob("*.png"))}


def _failures(project: Path) -> dict[str, Png]:
    folder = project / "output" / "screenshots" / "_failures"
    return {p.stem.rsplit("__", 1)[1]: Png(p) for p in sorted(folder.glob("*.png"))}


def _masked(png: Png) -> set[str]:
    """The boxes fully covered in ``png``; every other one must be fully visible."""
    covered = set()
    for name, ((x, y, w, h), colour) in BOXES.items():
        if y + h > png.height:
            continue
        seen = png.colours(x, y, w, h)
        assert seen in ({MASK_COLOR}, {colour}), f"{name} is partly covered: {seen}"
        if seen == {MASK_COLOR}:
            covered.add(name)
    return covered


def test_step_and_failure_captures_are_masked_to_the_pixel(project):
    output = _run(
        project,
        "masked",
        'mask: [data-testid="iban"]',
        _OPEN
        + """
### 2. Click
- role: editor
- action: click
- selector: '#go'
- screenshot: editor
- mask: [data-testid="phone"]

### 3. Fill
- role: editor
- action: fill-form
- fields:
    '[data-testid="first"]': one
    '[data-testid="second"]': two
- screenshot: editor
- interactive-progressive: true
- mask:
    - [data-testid="phone"]

### 4. Scroll away from what is masked
- role: editor
- action: scroll
- selector: '[data-testid="far-control"]'

### 5. The other role
- role: reviewer
- action: navigate
- target: /
- mask: [data-testid="far"]
"""
        + _FAILS,
    )
    shots = _captures(project, "masked")
    assert set(shots) == {"step-01", "step-02", "step-03-a", "step-03-b", "step-03-c"}
    # A step's own mask applies to its captures and to no other step's.
    assert _masked(shots["step-01"]) == {"project", "iban"}
    for stem in ("step-02", "step-03-a", "step-03-b", "step-03-c"):
        assert _masked(shots[stem]) == {"project", "iban", "phone"}, stem

    # The failure captures are full page, on both roles, with every mask the
    # scenario declares: the phone was masked two steps before the failure.
    failures = _failures(project)
    assert set(failures) == {"editor_page_noauth", "reviewer_page_noauth"}
    for role, png in failures.items():
        assert png.height > 2400, role
        assert _masked(png) == {"project", "iban", "phone", "far"}, role
    assert output.count("  screenshot: ") == 2


def test_a_project_mask_alone_covers_the_failure_capture(project):
    """The bug this fixes: `screenshot.mask` never reached it."""
    _run(project, "plain", "", _OPEN + _FAILS)
    assert _masked(_captures(project, "plain")["step-01"]) == {"project"}
    (png,) = _failures(project).values()
    assert _masked(png) == {"project"}


@pytest.mark.parametrize(
    "selector", ["'[data-testid='", "'xpath=//['", "'nope=1'"], ids=["css", "xpath", "unknown-engine"]
)
def test_a_bad_selector_yields_no_failure_image_and_a_reason(project, selector):
    """The bad mask sits on a step that never runs: the union carries it anyway.

    The image a first run left under the same name goes too, or the report would
    show a capture of another run for this failure.
    """
    unreached = "\n### 10. Unreached\n- role: editor\n- action: click\n- selector: '#go'\n- mask: "
    _run(project, "bad", "", _OPEN + _FAILS + unreached + "'.fine'\n")
    assert set(_failures(project)) == {"editor_page_noauth"}
    output = _run(project, "bad", "", _OPEN + _FAILS + unreached + f"{selector}\n")
    assert _failures(project) == {}
    assert "screenshot: not saved, the masked capture failed: Page.screenshot" in output


def _reported(project: Path) -> tuple[dict, list[Png]]:
    """The variant as the report shows it, and every failure image its site publishes."""
    site = project / "output" / "report"
    document = json.loads((site / "report.json").read_text(encoding="utf-8"))
    (scenario,) = document["scenarios"]
    (variant,) = scenario["variants"]
    return variant, [Png(p) for p in sorted((site / "assets" / "_failures").rglob("*.png"))]


@pytest.mark.parametrize("now", ["passes", "dies in setup", "fails again"])
def test_an_unmasked_image_of_an_earlier_run_is_never_this_runs(project, now):
    """Planted as a run of 0.9.2 left it: full page, nothing covered."""
    (project / "config.yaml").write_text(
        (project / "config.yaml").read_text(encoding="utf-8").replace("""- '[data-testid="project"]'""", "- '.none'"),
        encoding="utf-8",
    )
    _run(project, "stale", "", _OPEN + _FAILS)
    folder = project / "output" / "screenshots" / "_failures"
    (image,) = folder.glob("*.png")
    assert _masked(Png(image)) == set()
    other = folder / "tests_e2e_test_gone.py__test_gone(default-default)__editor_page_noauth.png"
    other.write_bytes(image.read_bytes())

    (project / "config.yaml").write_text(
        (project / "config.yaml").read_text(encoding="utf-8").replace("- '.none'", """- '[data-testid="project"]'"""),
        encoding="utf-8",
    )
    front, steps, outcome = {
        "passes": ("", _OPEN, "1 passed"),
        "dies in setup": ("seed: broken", _OPEN + _FAILS, "1 error"),
        "fails again": ("", _OPEN + _FAILS, "1 failed"),
    }[now]
    (project / "seed").mkdir()
    (project / "seed" / "broken.py").write_text("raise SystemExit(3)", encoding="utf-8")
    _run(project, "stale", front, steps, outcome)

    variant, published = _reported(project)
    if now == "fails again":
        assert _masked(Png(image)) == {"project"}
        assert [_masked(png) for png in published] == [{"project"}]
        assert variant["failure_screenshot"] is not None
    else:
        assert not image.exists()
        assert published == []
        assert variant["failure_screenshot"] is None
    # A test this run did not run: its image is left where it is, and published nowhere.
    assert other.exists()


def test_a_crashed_page_yields_no_failure_image_and_a_reason(project):
    output = _run(
        project,
        "crash",
        "",
        _OPEN
        + """
### 2. Crash
- role: editor
- action: custom
- code: |
    page.goto("chrome://crash")
""",
    )
    assert _failures(project) == {}
    assert "screenshot: not saved, the masked capture failed" in output
