"""A run publishes only the step screenshots it took itself.

Step screenshots stay on disk between runs, because guides are built from them.
So a run without ``--screenshots`` used to show an earlier run's as its own, in
its report, its bundle and its upload, even taken before a mask was declared.

The run's own event records now say which files it took, and their digest. They
are the only thing that does: a file's name and place are the same from one run
to the next, and the JSON sidecar beside it stays byte-stable on purpose, so a
content-addressed store still deduplicates it across runs.
"""

import hashlib
import json
import os
import shutil
import tarfile
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from steppy.bundle import create_bundle, inspect_bundle
from steppy.bundle.create import plan_bundle
from steppy.bundle.results import captures_from_events
from steppy.runmeta import new_run_id, run_started_at
from tests.test_bundle import RUN_ID, build_project, step_captures
from tests.test_failure_images import suite  # noqa: F401  (a fixture)

EVENTS = Path("output") / "events" / RUN_ID / "order-lifecycle__fr-mobile.jsonl"


def _manifest(root: Path, **kwargs):
    return plan_bundle(project_root=root, **kwargs).manifest


def _paths(manifest, *kinds: str) -> set[str]:
    return {f.path for f in manifest.files if f.kind in kinds}


def _forget(root: Path, *files: Path) -> None:
    """Drop the capture records of ``files`` (all of them by default): an earlier run took these."""
    names = {f.relative_to(root).as_posix() for f in files}
    path = root / EVENTS
    kept = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["kind"] == "capture" and (not files or row["path"] in names):
            continue
        kept.append(line)
    path.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")


def _left_out(manifest) -> list[str]:
    return [w for w in manifest.warnings if w.startswith("left out")]


# ── The generated conftest records what it takes ─────────────────────────────

_MODULE = """
from pathlib import Path

import pytest

from .conftest import take_screenshot, take_screenshot_with_bbox

SCENARIO = "demo"


class Page:
    url = "http://x/"
    viewport_size = {"width": 800, "height": 600}

    def title(self):
        return "T"

    def screenshot(self, path, **kwargs):
        Path(path).write_bytes(b"pixels of " + Path(path).name.encode())


@pytest.fixture
def editor_page(request):
    yield Page()


def test_demo(editor_page):
    take_screenshot_with_bbox(editor_page, SCENARIO, 1, "navigate")
    take_screenshot_with_bbox(editor_page, SCENARIO, 2, "fill-form", frame="b")
    take_screenshot(editor_page, SCENARIO, 3, "after")
"""

_TAKEN = {"step-01.png", "step-01.json", "step-02-b.png", "step-02-b.json", "step-03-after.png"}


def _capture(demo, **env) -> dict[str, str]:
    """Run the suite, and return what it recorded: file name → digest."""
    events = demo.root / "output" / "events" / RUN_ID
    output = demo.run("pass", STEPPY_SCREENSHOTS="1", **env)
    assert "1 passed" in output, output
    records = captures_from_events(events)
    assert all(path.startswith("output/") for path, _ in records)
    return {Path(path).name: digest for path, digest in records}


def test_every_capture_of_a_run_is_recorded_with_its_digest(suite):  # noqa: F811
    demo = suite(module=_MODULE)
    recorded = _capture(demo, STEPPY_EVENTS_DIR=str(demo.root / "output" / "events" / RUN_ID))
    folder = demo.root / "output" / "screenshots" / "demo"
    assert recorded == {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in folder.iterdir()}
    assert set(recorded) == _TAKEN


def test_the_step_records_opt_out_does_not_lose_them(suite):  # noqa: F811
    """`STEPPY_STEP_EVENTS=0` drops the per-step markers, not what the report shows."""
    demo = suite(module=_MODULE)
    events = str(demo.root / "output" / "events" / RUN_ID)
    assert set(_capture(demo, STEPPY_EVENTS_DIR=events, STEPPY_STEP_EVENTS="0")) == _TAKEN


def test_the_configured_screenshots_folder_is_recorded_as_it_is(suite):  # noqa: F811
    demo = suite("output: {screenshots_dir: ./output/shots}\n", module=_MODULE)
    events = demo.root / "output" / "events" / RUN_ID
    _capture(demo, STEPPY_EVENTS_DIR=str(events))
    assert {path.rsplit("/", 1)[0] for path, _ in captures_from_events(events)} == {"output/shots/demo"}


def test_a_bare_pytest_records_nothing_and_takes_the_same_files(suite):  # noqa: F811
    recorded = suite(module=_MODULE)
    assert _capture(recorded, STEPPY_EVENTS_DIR=str(recorded.root / "output" / "events" / RUN_ID))
    bare_root = recorded.root.parent / "bare"
    shutil.copytree(recorded.root, bare_root, ignore=shutil.ignore_patterns("output", "__pycache__"))
    bare = type(recorded)(bare_root)
    assert _capture(bare) == {}
    for f in (recorded.root / "output" / "screenshots").rglob("*.*"):
        # The sidecar carries no run id: the same bytes from one run to the next.
        assert (bare_root / f.relative_to(recorded.root)).read_bytes() == f.read_bytes()


def test_a_broken_record_never_fails_a_test(suite):  # noqa: F811
    demo = suite(module=_MODULE)
    blocked = demo.root / "output" / "events"
    blocked.parent.mkdir(parents=True)
    blocked.write_text("a file where the folder should be", encoding="utf-8")
    assert "1 passed" in demo.run("pass", STEPPY_SCREENSHOTS="1", STEPPY_EVENTS_DIR=str(blocked / RUN_ID))


# ── What the bundle keeps ────────────────────────────────────────────────────


def test_a_run_that_took_every_capture_on_disk_carries_them_all(tmp_path):
    root = build_project(tmp_path)
    manifest = _manifest(root)
    assert _paths(manifest, "screenshot", "sidecar") == {f.relative_to(root).as_posix() for f in step_captures(root)}
    assert _left_out(manifest) == []


def test_a_run_that_took_none_carries_none(tmp_path):
    root = build_project(tmp_path)
    _forget(root)
    manifest = _manifest(root)
    assert _paths(manifest, "screenshot", "sidecar") == set()
    images = [f for f in step_captures(root) if f.suffix == ".png"]
    assert f"left out {len(images)} step screenshot(s) this run did not take" in manifest.warnings
    # Left out of the bundle, never deleted: guides are built from them.
    assert all(f.is_file() for f in step_captures(root))


def test_the_captures_of_one_variant_do_not_bring_back_anothers(tmp_path):
    root = build_project(tmp_path)
    earlier = [f for f in step_captures(root) if "mobile" not in f.parts]
    _forget(root, *earlier)
    manifest = _manifest(root)
    assert _paths(manifest, "screenshot") == {
        f"output/screenshots/order-lifecycle/fr/mobile/{stem}.png"
        for stem in ("step-01", "step-02", "step-03-a", "step-03-b")
    }
    assert _paths(manifest, "sidecar") == {p.replace(".png", ".json") for p in _paths(manifest, "screenshot")}


def test_a_capture_something_else_overwrote_since_is_not_the_runs(tmp_path):
    """A `steppy rerun` or a bare `pytest` takes it again without recording it."""
    root = build_project(tmp_path)
    shot = root / "output" / "screenshots" / "order-lifecycle" / "fr" / "mobile" / "step-02.png"
    shot.write_bytes(b"taken by something else")
    manifest = _manifest(root)
    assert shot.relative_to(root).as_posix() not in _paths(manifest, "screenshot")
    assert "left out 1 step screenshot(s) this run did not take" in manifest.warnings


def test_an_image_kept_beside_the_captures_is_left_out_but_another_file_ships(tmp_path):
    """A `custom` step's own `page.screenshot(path=…)` shows the same pages."""
    root = build_project(tmp_path)
    folder = root / "output" / "screenshots" / "order-lifecycle"
    (folder / "by-hand.png").write_bytes(b"unmasked")
    (folder / "README.txt").write_text("mine", encoding="utf-8")
    manifest = _manifest(root)
    paths = {f.path for f in manifest.files}
    assert "output/screenshots/order-lifecycle/by-hand.png" not in paths
    assert "output/screenshots/order-lifecycle/README.txt" in paths


def test_an_earlier_run_still_on_disk_keeps_its_own(tmp_path):
    """`bundle create --run-id` names another recorded run: its records decide."""
    root = build_project(tmp_path)
    older = "20260805T090000Z-cccccc"
    shutil.copytree(root / EVENTS.parent, root / "output" / "events" / older)
    _forget(root)
    assert _paths(_manifest(root), "screenshot") == set()
    assert _paths(_manifest(root, run_id=older), "screenshot")


def test_a_tree_no_run_recorded_keeps_every_capture(tmp_path):
    """Nothing says whose they are, and the report is rebuilt from them."""
    root = build_project(tmp_path)
    shutil.rmtree(root / "output" / "events")
    shutil.rmtree(root / "output" / "junit")
    (root / "output" / ".steppy-run.json").unlink()
    manifest = _manifest(root)
    assert _paths(manifest, "screenshot") and _left_out(manifest) == []


def test_a_run_that_recorded_no_event_at_all_keeps_none(tmp_path):
    """A recorded run (its sidecar says so) whose tests never started took nothing."""
    root = build_project(tmp_path)
    shutil.rmtree(root / "output" / "events")
    assert _paths(_manifest(root), "screenshot") == set()


def test_a_torn_or_odd_record_is_no_record(tmp_path):
    root = build_project(tmp_path)
    _forget(root)
    with (root / EVENTS).open("a", encoding="utf-8") as fh:
        fh.write('{"kind": "capture", "path": 3, "sha256": "x"}\n{"kind": "capture"}\n["capture"]\n{"kind": "capt')
    assert captures_from_events((root / EVENTS).parent) == frozenset()


def test_a_run_asked_for_screenshots_that_recorded_none_is_told_why(tmp_path):
    """The likely cause after an upgrade: a conftest generated before captures were recorded."""
    root = build_project(tmp_path)
    _forget(root)
    sidecar = root / "output" / ".steppy-run.json"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["spec"]["screenshots"] is True
    assert any("asked for screenshots and recorded none" in w for w in _manifest(root).warnings)
    data["spec"]["screenshots"] = False
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    assert not any("asked for screenshots" in w for w in _manifest(root).warnings)


def test_the_archive_holds_what_the_manifest_lists(tmp_path):
    root = build_project(tmp_path)
    _forget(root)
    path = create_bundle(project_root=root, fmt="gz", target=tmp_path / "b.tar.gz").path
    assert not [f for f in inspect_bundle(path).files if f.kind in ("screenshot", "sidecar")]
    with tarfile.open(path, "r:gz") as tar:
        assert not [n for n in tar.getnames() if n.startswith("output/screenshots/order-lifecycle")]


# ── Guides ───────────────────────────────────────────────────────────────────


def _guides(root: Path) -> dict[str, Path]:
    """The fixture's guide pages, plus the files a guide can hold pixels in."""
    folder = root / "output" / "guides" / "editor"
    for name in ("fr/mobile/order-lifecycle.pdf", "fr/mobile/order-lifecycle.mp4", "editor-handbook.html"):
        (folder / name).write_bytes(b"pixels")
    (folder / "fr" / "mobile" / "onboarding.html").write_text("<html/>", encoding="utf-8")
    return {f.name: f for f in folder.rglob("*") if f.is_file()}


def _guide_names(manifest) -> set[str]:
    kinds = ("guide_html", "guide_md", "guide_asset", "interactive_manifest")
    return {Path(f.path).name for f in manifest.files if f.kind in kinds}


def test_the_guides_of_a_run_that_took_every_capture_stay(tmp_path):
    root = build_project(tmp_path)
    guides = _guides(root)
    assert _guide_names(_manifest(root)) == set(guides)


def test_the_guides_of_a_scenario_with_earlier_captures_go(tmp_path):
    """Their images are left out, so the pages would be broken; a PDF or a video holds the pixels."""
    root = build_project(tmp_path)
    guides = _guides(root)
    _forget(root, *(f for f in step_captures(root) if "order-lifecycle" in f.parts))
    manifest = _manifest(root)
    # The other scenario's page is whole, and is the only file that can be told apart.
    assert _guide_names(manifest) == {"onboarding.html"}
    left = len(guides) - 1
    assert (
        f"left out {left} guide file(s) built before this run or from screenshots it did not take" in manifest.warnings
    )


def test_a_guide_built_before_the_run_goes(tmp_path):
    """The run took every capture again, masked; the PDF still holds the earlier pixels."""
    root = build_project(tmp_path)
    before = run_started_at(RUN_ID) - 60
    for guide in _guides(root).values():
        if guide.suffix == ".pdf":
            os.utime(guide, (before, before))
    manifest = _manifest(root)
    assert "order-lifecycle.pdf" not in _guide_names(manifest)
    assert "order-lifecycle.html" in _guide_names(manifest)


def test_a_run_id_of_another_shape_has_no_start(tmp_path):
    assert run_started_at("ci-build-42") is None
    assert run_started_at("20269999T999999Z-aaaaaa") is None
    assert abs(run_started_at(new_run_id()) - time.time()) < 5


# ── The report and the upload ────────────────────────────────────────────────


def _report(root: Path, monkeypatch, *args: str) -> tuple[str, dict]:
    from steppy.cli import app

    monkeypatch.chdir(root)
    result = CliRunner().invoke(app, ["report", "-o", "site", "--screenshots", "all", "--force", *args])
    assert result.exit_code == 0, result.output
    document = json.loads((root / "site" / "report.json").read_text(encoding="utf-8"))
    return (root / "site" / "index.html").read_text(encoding="utf-8"), document


def _notice(page: str) -> int:
    return " ".join(page.split()).count("This run took <strong>no step screenshots</strong>")


def test_the_report_publishes_none_of_an_earlier_runs_and_says_so_once(tmp_path, monkeypatch):
    root = build_project(tmp_path)
    _forget(root)
    page, document = _report(root, monkeypatch)
    published = [f.name for f in (root / "site").rglob("*.png") if "_failures" not in f.parts]
    assert not [name for name in published if name.startswith("step-")]
    assert not any(step["screenshots"] for s in document["scenarios"] for v in s["variants"] for step in v["steps"])
    assert document["capabilities"]["step_screenshots"] is False
    assert _notice(page) == 1
    assert sum(_notice(f.read_text(encoding="utf-8")) for f in (root / "site" / "scenarios").glob("*.html")) == 0


def test_the_report_of_a_run_that_took_some_says_nothing(tmp_path, monkeypatch):
    root = build_project(tmp_path)
    page, document = _report(root, monkeypatch)
    assert document["capabilities"]["step_screenshots"] is True and _notice(page) == 0
    # Whatever the site copies: the fact is about the run, not about the policy.
    page, document = _report(root, monkeypatch, "--screenshots", "none")
    assert document["capabilities"]["step_screenshots"] is True and _notice(page) == 0


def test_the_report_of_an_unrecorded_tree_keeps_its_own_notice_only(tmp_path, monkeypatch):
    root = build_project(tmp_path)
    for name in ("events", "junit"):
        shutil.rmtree(root / "output" / name)
    (root / "output" / ".steppy-run.json").unlink()
    page, document = _report(root, monkeypatch)
    assert "no test outcomes" in page and _notice(page) == 0
    assert document["scenarios"], "rebuilt from the captures on disk"


def test_an_upload_offers_none_of_an_earlier_runs(tmp_path):
    from steppy.cloud.upload import TreeSource, attach_report

    root = build_project(tmp_path)
    _forget(root)
    source, model = attach_report(TreeSource(plan_bundle(project_root=root)), project_root=root)
    kinds = {f.kind for f in source.manifest.files}
    assert "report" in kinds and not kinds & {"screenshot", "sidecar"}
    assert model.capabilities.step_screenshots is False


@pytest.mark.parametrize("name", ["step-01.png", "step-01.json"])
def test_the_record_names_a_file_by_its_manifest_path(tmp_path, name):
    """One spelling on both sides, or every capture would look like an earlier run's."""
    root = build_project(tmp_path)
    recorded = {path for path, _ in captures_from_events((root / EVENTS).parent)}
    assert f"output/screenshots/order-lifecycle/fr/mobile/{name}" in recorded & {f.path for f in _manifest(root).files}
