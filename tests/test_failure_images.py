"""Failure screenshots never outlive the run that took them (spec I7).

``output/screenshots/_failures/`` is not cleared between runs, and everything
that reads it joins an image to a test by name. So an image left by an earlier
run, possibly taken before the project declared its masks, used to be shown,
bundled and uploaded as the current run's, whenever the test now passed or died
before its body.

Two halves, each pinned here:

  * the generated conftest: a test drops its own earlier images before its setup,
    under a real pytest (a retry in the same process included);
  * the bundle (which the report and the upload read): an image of a test the
    run did not run stays out.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from steppy.bundle.create import plan_bundle
from steppy.generator import SteppyGenerator
from steppy.models import AppAuthConfig, AppConfig, SteppyConfig
from steppy.parser import parse_scenario_file
from tests.test_bundle import RUN_ID, SANITISED, build_project

ROOT = Path(__file__).resolve().parents[1]

_MODULE = """
import os
from pathlib import Path

import pytest

SCENARIO = "demo"
CASES = os.environ["CASES"].split(",")  # what each attempt does
CASES_DONE = []


class Page:
    url = "http://x/"

    def title(self):
        return "T"

    def screenshot(self, path, **kwargs):
        Path(path).write_text(f"this run, attempt {len(CASES_DONE)}", encoding="utf-8")


@pytest.fixture
def editor_page(request):
    CASES_DONE.append(CASES[len(CASES_DONE)])
    if CASES_DONE[-1] == "setup":
        raise RuntimeError("login failed")
    yield Page()


def test_demo(editor_page):
    assert CASES_DONE[-1] == "pass"


def test_demo__more(editor_page):
    pass


@pytest.mark.skip(reason="not now")
def test_skipped(editor_page):
    pass


def test_without_a_page():
    pass
"""

#: What pytest-rerunfailures does (it is an optional extra): run the protocol
#: again in the same process, with the failed attempt's reports as ``rerun``.
_RERUN_PLUGIN = """
import pytest
from _pytest.runner import runtestprotocol


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    reports = runtestprotocol(item, nextitem=nextitem, log=False)
    if any(r.failed for r in reports):
        reports = runtestprotocol(item, nextitem=nextitem, log=False)
    for report in reports:
        item.ihook.pytest_runtest_logreport(report=report)
    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True
"""

_DEMO = "tests_e2e_test_demo.py__test_demo(default-default)"


class _Suite:
    def __init__(self, root: Path, screenshots: str = "output/screenshots"):
        self.root = root
        self.failures = root / screenshots / "_failures"

    def plant(self, name: str) -> Path:
        path = self.failures / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("an earlier run", encoding="utf-8")
        return path

    def images(self) -> dict[str, str]:
        if not self.failures.is_dir():
            return {}
        return {p.name: p.read_text(encoding="utf-8") for p in sorted(self.failures.iterdir()) if p.is_file()}

    def run(self, cases: str, *select: str, reruns: bool = False) -> str:
        args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--browser", "chromium"]
        if reruns:
            args += ["-p", "rerun_once"]
        env = {k: v for k, v in os.environ.items() if not k.startswith("STEPPY_")}
        result = subprocess.run(
            [*args, *(select or ["tests/e2e/test_demo.py::test_demo"])],
            cwd=self.root,
            env={**env, "CASES": cases, "PYTHONPATH": str(self.root), "PYTHONIOENCODING": "utf-8"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        return result.stdout + result.stderr


@pytest.fixture
def suite(tmp_path):
    def make(config: str = "") -> _Suite:
        root = tmp_path / "project"
        e2e = root / "tests" / "e2e"
        e2e.mkdir(parents=True)
        (root / "config.yaml").write_text(f"apps: {{}}\n{config}", encoding="utf-8")
        scenario = root / "demo.md"
        scenario.write_text("---\ntitle: T\n---\n\n### 1. S\n- role: editor\n- action: login\n", encoding="utf-8")
        generator = SteppyGenerator(
            SteppyConfig(apps={"editor": AppConfig(name="N", url="http://x", auth=AppAuthConfig(mode="bypass"))}),
            templates_dir=ROOT / "templates",
        )
        for name, text in {
            e2e.parent / "__init__.py": "",
            e2e / "__init__.py": "",
            e2e / "conftest.py": generator.generate_conftest([parse_scenario_file(scenario)]),
            e2e / "test_demo.py": textwrap.dedent(_MODULE),
            root / "rerun_once.py": _RERUN_PLUGIN,
        }.items():
            name.write_text(text, encoding="utf-8")
        return _Suite(root)

    return make


# ── The generated conftest, under a real pytest ──────────────────────────────


def test_a_test_that_passes_now_drops_the_image_of_its_earlier_failure(suite):
    demo = suite()
    demo.plant(f"{_DEMO}__editor_page.png")
    assert "1 passed" in demo.run("pass")
    assert demo.images() == {}


def test_a_test_that_dies_in_setup_drops_it_too(suite):
    """Login, a seed script, a fixture: the hook takes no capture there."""
    demo = suite()
    demo.plant(f"{_DEMO}__editor_page.png")
    assert "1 error" in demo.run("setup")
    assert demo.images() == {}


def test_a_skipped_test_drops_it_too(suite):
    demo = suite()
    demo.plant("tests_e2e_test_demo.py__test_skipped(default-default)__editor_page.png")
    assert "1 skipped" in demo.run("pass", "tests/e2e/test_demo.py::test_skipped")
    assert demo.images() == {}


def test_a_test_that_fails_again_leaves_only_its_new_image(suite):
    demo = suite()
    demo.plant(f"{_DEMO}__editor_page.png")
    assert "1 failed" in demo.run("fail")
    assert demo.images() == {f"{_DEMO}__editor_page.png": "this run, attempt 1"}


def test_the_images_of_roles_the_scenario_no_longer_has_go_too(suite):
    """A renamed role, or a scenario that turned `auth: false`: nothing would overwrite them."""
    demo = suite()
    for fixture in ("editor_page_noauth", "reviewer_page", "old_role_page"):
        demo.plant(f"{_DEMO}__{fixture}.png")
    demo.run("pass")
    assert demo.images() == {}


def test_nothing_else_is_touched(suite):
    """Another test of this run, in this worker or another, may have just written it."""
    demo = suite()
    others = {
        # A test whose id extends this one: the tour replay is one.
        "tests_e2e_test_demo.py__test_demo__tour(default-default)__editor_page.png",
        "tests_e2e_test_demo.py__test_demo__more(default-default)__editor_page.png",
        # The same test in another locale or layout.
        "tests_e2e_test_demo.py__test_demo(fr-mobile)__editor_page.png",
        # Another scenario, and one whose slug ends like this one.
        "tests_e2e_test_other.py__test_other(default-default)__editor_page.png",
        "tests_e2e_test_my-demo.py__test_demo(default-default)__editor_page.png",
        # Not a failure image of this conftest's.
        f"{_DEMO}__notes.txt",
        f"{_DEMO}__editor_page.png.bak",
        f"{_DEMO}.png",
        "README.md",
    }
    for name in others:
        demo.plant(name)
    demo.run("pass")
    assert set(demo.images()) == others


def test_an_id_this_one_extends_is_another_test_too(suite):
    demo = suite()
    demo.plant(f"{_DEMO}__editor_page.png")
    assert "1 passed" in demo.run("pass", "tests/e2e/test_demo.py::test_demo__more")
    assert set(demo.images()) == {f"{_DEMO}__editor_page.png"}


def test_a_second_test_of_the_same_run_keeps_the_first_ones_image(suite):
    demo = suite()
    output = demo.run("fail,pass", "tests/e2e/test_demo.py::test_demo", "tests/e2e/test_demo.py::test_demo__more")
    assert "1 failed, 1 passed" in output
    assert set(demo.images()) == {f"{_DEMO}__editor_page.png"}


def test_a_test_without_a_page_and_a_project_without_failures(suite):
    demo = suite()
    assert "1 passed" in demo.run("pass", "tests/e2e/test_demo.py::test_without_a_page")
    assert not demo.failures.exists()  # nothing is created to look in


def test_the_configured_screenshots_folder_is_the_one_cleared(suite):
    demo = suite("output: {screenshots_dir: ./shots}\n")
    elsewhere = demo.plant(f"{_DEMO}__editor_page.png")
    demo.failures = demo.root / "shots" / "_failures"
    demo.plant(f"{_DEMO}__editor_page.png")
    demo.run("pass")
    assert demo.images() == {}
    assert elsewhere.is_file()


def test_an_image_that_cannot_be_removed_is_printed_and_fails_no_test(suite):
    """A folder in its place cannot be unlinked on any system; a Windows lock is the same error."""
    demo = suite()
    (demo.failures / f"{_DEMO}__editor_page.png").mkdir(parents=True)
    output = demo.run("pass")
    assert "1 passed" in output
    assert "an earlier failure screenshot could not be removed" in output


# ── Retries ──────────────────────────────────────────────────────────────────


def test_a_retry_that_passes_keeps_the_failed_attempts_image(suite):
    """The events keep both attempts, and the report shows the failed one's text: so too its image."""
    demo = suite()
    demo.plant(f"{_DEMO}__editor_page.png")
    demo.plant(f"{_DEMO}__old_role_page.png")
    assert "1 passed" in demo.run("fail,pass", reruns=True)
    assert demo.images() == {f"{_DEMO}__editor_page.png": "this run, attempt 1"}


def test_a_retry_that_fails_leaves_the_last_attempts_image(suite):
    demo = suite()
    assert "1 failed" in demo.run("fail,fail", reruns=True)
    assert demo.images() == {f"{_DEMO}__editor_page.png": "this run, attempt 2"}


def test_a_retry_that_dies_in_setup_leaves_no_image(suite):
    """Its failure text is the setup's; the first attempt's page is not what it describes."""
    demo = suite()
    assert "1 error" in demo.run("fail,setup", reruns=True)
    assert demo.images() == {}


def test_a_retry_after_a_setup_failure_still_clears_the_earlier_runs(suite):
    demo = suite()
    demo.plant(f"{_DEMO}__editor_page.png")
    assert "1 passed" in demo.run("setup,pass", reruns=True)
    assert demo.images() == {}


def test_the_retry_pass_is_a_new_process_and_starts_over(suite):
    """`--retry-pass` replaces the scenario's events and JUnit; its image goes the same way."""
    demo = suite()
    demo.run("fail")
    assert demo.images()
    demo.run("pass")
    assert demo.images() == {}


# ── The bundle, the report and the upload ────────────────────────────────────

_STALE = "tests_e2e_test_gone.py__test_gone(fr-mobile)__editor_page.png"


def _failure_rows(plan) -> dict[str, str]:
    return {Path(f.path).name: f.scenario for f in plan.assembly.manifest.files if f.kind == "failure_screenshot"}


def _plan(root: Path):
    return plan_bundle(project_root=root, run_id=RUN_ID)


def test_an_image_of_a_test_this_run_did_not_run_stays_out_of_the_bundle(tmp_path):
    root = build_project(tmp_path)
    folder = root / "output" / "screenshots" / "_failures"
    (folder / _STALE).write_text("unmasked, from last week", encoding="utf-8")
    plan = _plan(root)
    assert _failure_rows(plan) == {f"{SANITISED}__editor_page.png": "order-lifecycle"}
    assert "left out 1 failure screenshot(s) of tests this run did not run" in plan.assembly.manifest.warnings
    assert (folder / _STALE).is_file()  # left out, not deleted: it is not this run's


def test_nothing_is_said_when_nothing_is_left_out(tmp_path):
    assert not [w for w in _plan(build_project(tmp_path)).assembly.manifest.warnings if "failure screenshot" in w]


def test_a_run_that_recorded_no_test_keeps_every_image(tmp_path):
    """Nothing says whose they are, and dropping the only evidence would be worse."""
    import shutil

    root = build_project(tmp_path)
    shutil.rmtree(root / "output" / "events")
    shutil.rmtree(root / "output" / "junit", ignore_errors=True)
    (root / "output" / "screenshots" / "_failures" / _STALE).write_text("x", encoding="utf-8")
    assert set(_failure_rows(_plan(root))) == {f"{SANITISED}__editor_page.png", _STALE}


def test_the_report_does_not_copy_it_into_its_site_either(tmp_path, monkeypatch):
    """`--screenshots failed` copies every failure image of the manifest, attributed or not."""
    from typer.testing import CliRunner

    from steppy.cli import app

    root = build_project(tmp_path)
    folder = root / "output" / "screenshots" / "_failures"
    (folder / _STALE).write_text("unmasked, from last week", encoding="utf-8")
    monkeypatch.chdir(root)
    result = CliRunner().invoke(app, ["report", "-o", "site"])
    assert result.exit_code == 0, result.output
    copied = [p.read_bytes() for p in (root / "site" / "assets").rglob("*.png")]
    assert b"failshot" in copied and b"unmasked, from last week" not in copied
