"""Opt-in browser integration test: the generated tour replays (spec M4).

Skipped unless ``STEPPY_TOUR_E2E=1`` (needs Playwright Chromium and the
built overlay, ``npm --prefix packages/player run build``). The real loop:

    steppy generate (fixtures/tour-demo/project, in a temp copy)
    -> tests/e2e/test_editor--*.py with test_<slug>__tour replays
    -> pytest against the demo app (fixtures/tour-demo/serve.py)

A renamed test-id in the app must fail the replay with a message naming the
tour, the step and the test-id — the CI signal the feature exists for.

Each demo scenario declares ``layouts: [desktop, mobile]``, so every replay
runs at 1280x800 and at 390x844; on the narrow one the customers tour goes
through the app's menu drawer instead of its header link.

Run it:  STEPPY_TOUR_E2E=1 python -m pytest tests/integration/test_tour_replay_it.py -q
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.skipif(
    os.environ.get("STEPPY_TOUR_E2E") != "1",
    reason="browser integration test — set STEPPY_TOUR_E2E=1 to run",
)

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "fixtures" / "tour-demo"


def _serve_module():
    spec = importlib.util.spec_from_file_location("tour_demo_serve", DEMO / "serve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


serve = _serve_module()


def _require_bundle() -> None:
    bundle = Path(os.environ.get("STEPPY_TOUR_BUNDLE") or serve.DEFAULT_BUNDLE)
    if not bundle.is_file():
        pytest.fail(serve.BUILD_HINT)


def _generated(tmp_path_factory, name: str, edit=None) -> Path:
    _require_bundle()
    from steppy.cli import app

    root = tmp_path_factory.mktemp(name)
    shutil.copytree(DEMO / "project", root, dirs_exist_ok=True)
    if edit:
        edit(root)
    cwd = os.getcwd()
    os.chdir(root)
    try:
        result = CliRunner().invoke(app, ["generate", "-o", "tests/e2e"])
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    assert (root / "app-assets" / "tours" / "tours.manifest.json").is_file()
    return root


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    return _generated(tmp_path_factory, "tour-demo")


def _last_step_advances_on_visible(root: Path) -> None:
    # Step 4's target (the status) is on screen before the step is reached, so
    # the overlay passes it in the same pass, without ever drawing its popover.
    scenario = root / "scenarios" / "editor--send-invoice.md"
    text = scenario.read_text(encoding="utf-8")
    marker = "### 4. The invoice is sent\n- role: editor\n- action: assert\n"
    assert marker in text
    scenario.write_text(text.replace(marker, marker + "- tour-advance: visible\n"), encoding="utf-8")


@pytest.fixture(scope="module")
def visible_project(tmp_path_factory) -> Path:
    return _generated(tmp_path_factory, "tour-demo-visible", _last_step_advances_on_visible)


def _start_on_the_customer_list(entry: str):
    # The guide walks from the invoice list to the customers (header link, or the
    # menu drawer on a phone); the tour starts on the customer list and ends on
    # the confirmation, so step 7 runs after it.
    def edit(root: Path) -> None:
        scenario = root / "scenarios" / "editor--remove-inactive-customers.md"
        text = scenario.read_text(encoding="utf-8")
        title = "  title: {en: Remove inactive customers, fr: Retirer les clients inactifs}\n"
        remove = "- selector: '[data-testid=\"remove-inactive-btn\"]'\n"
        for old, new in (
            (title, title + f"  entry: {entry}\n"),
            (remove, remove + "- tour: start\n"),
            ("- action: confirm\n- tour: keep\n", "- action: confirm\n- tour: [end, keep]\n"),
        ):
            assert old in text
            text = text.replace(old, new)
        scenario.write_text(text, encoding="utf-8")

    return edit


@pytest.fixture(scope="module")
def ranged_project(tmp_path_factory) -> Path:
    return _generated(tmp_path_factory, "tour-demo-ranged", _start_on_the_customer_list("/customers"))


@pytest.fixture(scope="module")
def wrong_entry_project(tmp_path_factory) -> Path:
    return _generated(tmp_path_factory, "tour-demo-wrong-entry", _start_on_the_customer_list("/invoices"))


def _run(project: Path, url: str, *args: str, **env: str) -> subprocess.CompletedProcess:
    environ = {k: v for k, v in os.environ.items() if not k.startswith("STEPPY_") or k == "STEPPY_TOUR_BUNDLE"}
    environ.update({"TOUR_DEMO_URL": url, **env})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/e2e", "-q", "-p", "no:cacheprovider", *args],
        cwd=project,
        env=environ,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _demo(project: Path, **config):
    return serve.DemoServer(tours_dir=project / "app-assets" / "tours", config=config or None)


def test_the_scenarios_and_their_replays_pass(project):
    with _demo(project) as demo:
        proc = _run(project, demo.url, "-rA")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "12 passed" in proc.stdout
    passed = {line.rsplit("::", 1)[-1] for line in proc.stdout.splitlines() if line.startswith("PASSED ")}
    tours = ("create_invoice", "send_invoice", "remove_inactive_customers")
    assert {f"test_editor__{t}__tour[default-{size}]" for t in tours for size in ("desktop", "mobile")} <= passed


def test_a_renamed_test_id_fails_the_replay_naming_tour_step_and_test_id(project):
    with _demo(project, testIdOverrides={"invoice-title": "invoice-name"}, targetTimeout=1500) as demo:
        proc = _run(project, demo.url, "-k", "create_invoice__tour")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "[tour create-invoice] step 'invoice-title'" in out
    assert 'test-id "invoice-title"' in out
    assert "target-timeout" in out


def test_the_strict_build_id_check_is_opt_in(project):
    with _demo(project) as demo:
        proc = _run(project, demo.url, "-k", "create_invoice__tour", STEPPY_TOUR_EXPECT_BUILD_ID="other-0002")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "the app serves tours built for 'demo-0001', expected 'other-0002'" in out


def test_the_kill_switch_keeps_an_auto_started_tour_off_ordinary_tests(project):
    # The app starts a tour on every page load; the scenario tests must not see it,
    # and the replay still drives its own.
    with _demo(project, autoStart="create-invoice") as demo:
        proc = _run(project, demo.url, "-k", "create_invoice")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "4 passed" in proc.stdout


def test_guide_runs_skip_the_replays(project):
    with _demo(project) as demo:
        proc = _run(project, demo.url, "--collect-only", STEPPY_GUIDE_MODE="1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "__tour" not in proc.stdout.split("collected")[0]


def test_a_tour_started_on_a_page_reached_by_clicking_replays_in_place(ranged_project):
    tour = json.loads(
        (ranged_project / "app-assets" / "tours" / "remove-inactive-customers.en.json").read_text("utf-8")
    )
    assert tour["entry"] == {"route": "/customers"}
    assert [s["id"] for s in tour["steps"]] == ["remove-inactive-btn", "confirm"]
    with _demo(ranged_project) as demo:
        proc = _run(ranged_project, demo.url, "-k", "remove_inactive_customers")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "4 passed" in proc.stdout  # the scenario test and its replay, on both layouts


def test_an_entry_that_is_not_the_page_the_start_is_on_fails_the_replay(wrong_entry_project):
    with _demo(wrong_entry_project) as demo:
        proc = _run(wrong_entry_project, demo.url, "-k", "remove_inactive_customers__tour and desktop")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "[tour remove-inactive-customers]" in out
    assert "navigated to its entry route /invoices instead of starting in place" in out
    assert "reached (/customers) does not match tour.entry /invoices" in out


def test_a_visible_step_whose_target_is_already_there_replays(visible_project):
    with _demo(visible_project) as demo:
        proc = _run(visible_project, demo.url, "-k", "send_invoice__tour")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "2 passed" in proc.stdout


# ── Hover menus: `tour-hold` ──────────────────────────────────────────────────

# The demo's /reports page has a rail that expands while hovered and collapses on
# mouseleave; its submenu only shows while expanded. `tour-placement: top` on the
# link puts that step's popover over the toggle, right under the pointer that just
# clicked it: without a hold zone the rail collapses and the link disappears.
_HOVER_MENU = """---
title: Open the monthly report
description: An editor opens a report from the hover side menu.
tags: [tour-demo]
layouts: [desktop]
tour:
  id: monthly-report
  title: {en: Open the monthly report, fr: Ouvrir le rapport mensuel}
  entry: /reports
---

### 1. Open the reports
- role: editor
- action: navigate
- target: /reports

### 2. Open the side menu
- role: editor
- action: hover
- selector: '[data-testid="side-nav"]'

### 3. Open the reports submenu
- role: editor
- action: click
- selector: '[data-testid="side-reports-toggle"]'
HOLD- tour-text: {en: "Open **Reports**.", fr: "Ouvrez **Rapports**."}

### 4. Pick the monthly report
- role: editor
- action: click
- selector: '[data-testid="side-report-monthly"]'
- tour-placement: top
HOLD- expect: contains([data-testid="report-title"], "Monthly report")
- tour-text: {en: "Pick **Monthly report**.", fr: "Choisissez **Rapport mensuel**."}
"""


def _hover_menu(hold: bool):
    def edit(root: Path) -> None:
        line = "- tour-hold: '[data-testid=\"side-nav\"]'\n" if hold else ""
        (root / "scenarios" / "editor--monthly-report.md").write_text(
            _HOVER_MENU.replace("HOLD", line), encoding="utf-8"
        )

    return edit


@pytest.fixture(scope="module")
def held_project(tmp_path_factory) -> Path:
    return _generated(tmp_path_factory, "tour-demo-held", _hover_menu(hold=True))


@pytest.fixture(scope="module")
def unheld_project(tmp_path_factory) -> Path:
    return _generated(tmp_path_factory, "tour-demo-unheld", _hover_menu(hold=False))


def _validate(project: Path) -> str:
    from steppy.cli import app

    cwd = os.getcwd()
    os.chdir(project)
    try:
        return CliRunner().invoke(app, ["validate"]).output
    finally:
        os.chdir(cwd)


def test_a_held_hover_menu_survives_the_tour(held_project):
    tour = json.loads((held_project / "app-assets" / "tours" / "monthly-report.en.json").read_text("utf-8"))
    assert [(s["id"], s.get("hold")) for s in tour["steps"]] == [
        ("side-reports-toggle", "side-nav"),
        ("side-report-monthly", "side-nav"),
    ]
    assert "hover-collapse-risk" not in _validate(held_project)
    with _demo(held_project) as demo:
        proc = _run(held_project, demo.url, "-k", "monthly_report")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "2 passed" in proc.stdout  # the scenario test and its replay, desktop only


def test_an_unheld_hover_menu_warns_and_collapses_in_the_replay(unheld_project):
    assert "hover-collapse-risk" in _validate(unheld_project)
    with _demo(unheld_project, targetTimeout=1500) as demo:
        proc = _run(unheld_project, demo.url, "-k", "monthly_report__tour")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "[tour monthly-report] step 'side-report-monthly'" in out
    assert "the tour went off-path: its target disappeared" in out
