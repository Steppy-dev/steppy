"""``steppy report`` end to end through the CLI, over all three input modes.

Uses the shared bundle fixture, so this is the closest thing to a real run in the
suite without a browser: a real ``output/`` tree, a real archive, and the same
manifest either way.
"""

import json
import tarfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from steppy.bundle import create_bundle
from steppy.cli import app

from .test_bundle import build_project

runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return build_project(tmp_path)


def _run(*args) -> "object":
    return runner.invoke(app, ["report", *[str(a) for a in args]])


def _site(root: Path) -> Path:
    return root / "output" / "report"


# ── the three input modes ───────────────────────────────────────────────────


def test_reporting_the_live_output_tree(project: Path):
    result = _run("--project-root", project)
    assert result.exit_code == 0, result.output
    site = _site(project)
    assert (site / "index.html").is_file()
    assert (site / "report.json").is_file()
    assert (site / "assets" / "report.css").is_file()


def test_reporting_an_archive(project: Path, tmp_path: Path):
    archive = create_bundle(project_root=project, fmt="gz").path
    out = tmp_path / "from-archive"
    result = _run(archive, "--project-root", project, "-o", out)
    assert result.exit_code == 0, result.output
    assert (out / "index.html").is_file()
    assert json.loads((out / "report.json").read_text(encoding="utf-8"))["source"]["kind"] == "archive"


def test_reporting_an_unpacked_bundle_directory(project: Path, tmp_path: Path):
    archive = create_bundle(project_root=project, fmt="gz").path
    unpacked = tmp_path / "unpacked"
    with tarfile.open(archive, mode="r:gz") as tar:
        tar.extractall(unpacked, filter="data")
    out = tmp_path / "from-dir"
    result = _run(unpacked, "--project-root", project, "-o", out)
    assert result.exit_code == 0, result.output
    assert json.loads((out / "report.json").read_text(encoding="utf-8"))["source"]["kind"] == "dir"


def test_the_three_modes_agree_on_everything_they_can(project: Path, tmp_path: Path):
    """Everything downstream of ``ReportSource`` is written once, assuming this.

    Two fields legitimately differ and are excluded rather than papered over:
    ``source`` says where the run came from, and ``coverage.warnings`` can carry a
    live-store diagnostic that only the tree mode is even allowed to attempt —
    reading a local ``.steppy/coverage.db`` for someone else's downloaded bundle is
    exactly what the tiering forbids.
    """
    archive = create_bundle(project_root=project, fmt="gz").path
    unpacked = tmp_path / "unpacked"
    with tarfile.open(archive, mode="r:gz") as tar:
        tar.extractall(unpacked, filter="data")

    reports = []
    for i, arg in enumerate((None, archive, unpacked)):
        out = tmp_path / f"site{i}"
        args = ["--project-root", project, "-o", out]
        result = _run(*([arg] + args if arg else args))
        assert result.exit_code == 0, result.output
        payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
        payload.pop("source")
        payload["coverage"].pop("warnings")
        reports.append(payload)
    assert reports[0] == reports[1] == reports[2]


def test_no_absolute_filesystem_path_reaches_the_published_files(project: Path, tmp_path: Path):
    """``report.json`` and the page footer both get published. An absolute path
    there leaks a home directory or a CI runner's layout to anyone who opens the
    site, and it cannot be taken back once indexed."""
    out = tmp_path / "site"
    assert _run("--project-root", project, "-o", out).exit_code == 0
    payload = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert not Path(payload["source"]["label"]).is_absolute()
    for name in ("index.html", "report.json"):
        text = (out / name).read_text(encoding="utf-8")
        assert str(project) not in text, name
        assert str(tmp_path) not in text, name


# ── flags ───────────────────────────────────────────────────────────────────


def test_json_prints_the_model_and_writes_nothing_extra(project: Path, tmp_path: Path):
    out = tmp_path / "site"
    result = _run("--project-root", project, "-o", out, "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["steppy_report"] == 1


def test_dry_run_writes_no_files(project: Path, tmp_path: Path):
    out = tmp_path / "site"
    result = _run("--project-root", project, "-o", out, "--dry-run")
    assert result.exit_code == 0, result.output
    assert not out.exists()
    assert "Would render" in result.output


def test_screenshots_all_copies_more_than_failed(project: Path, tmp_path: Path):
    counts = {}
    for policy in ("failed", "all"):
        out = tmp_path / policy
        assert _run("--project-root", project, "-o", out, "--screenshots", policy).exit_code == 0
        counts[policy] = len(list((out / "assets").rglob("*.png"))) if (out / "assets").exists() else 0
    assert counts["all"] > counts["failed"]


def test_screenshots_none_copies_nothing(project: Path, tmp_path: Path):
    out = tmp_path / "site"
    assert _run("--project-root", project, "-o", out, "--screenshots", "none").exit_code == 0
    assert list((out / "assets").rglob("*.png")) == []


def test_an_unknown_screenshot_policy_is_a_clean_error(project: Path, tmp_path: Path):
    result = _run("--project-root", project, "-o", tmp_path / "site", "--screenshots", "some")
    assert result.exit_code == 1
    assert "--screenshots must be one of" in result.output
    assert "Traceback" not in result.output


def test_an_unknown_badge_surface_is_a_clean_error(project: Path, tmp_path: Path):
    result = _run("--project-root", project, "-o", tmp_path / "site", "--badge-surface", "everything")
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_a_custom_title_reaches_the_page(project: Path, tmp_path: Path):
    out = tmp_path / "site"
    assert _run("--project-root", project, "-o", out, "--title", "Nightly E2E").exit_code == 0
    assert "Nightly E2E" in (out / "index.html").read_text(encoding="utf-8")


def test_a_missing_bundle_path_is_a_clean_error(project: Path, tmp_path: Path):
    result = _run(tmp_path / "ghost.tar.gz", "--project-root", project)
    assert result.exit_code == 1
    assert "no such bundle" in result.output
    assert "Traceback" not in result.output


# ── the exit code describes rendering, not the run ──────────────────────────


def test_a_report_of_a_failing_run_still_exits_zero(project: Path, tmp_path: Path):
    """A report step that goes red for a red run is a step people delete. The
    verdict lives in the summary and in ``report.json``'s totals.exit_code."""
    events = project / "output" / "events" / "20260806T101500Z-a1b2c3"
    events.mkdir(parents=True, exist_ok=True)
    nodeid = "tests/e2e/test_order-lifecycle.py::test_flow[fr-mobile]"
    lines = [
        {"v": 1, "kind": "test-start", "ts": 1.0, "nodeid": nodeid, "scenario": "order-lifecycle"},
        {"v": 1, "kind": "test-end", "ts": 2.0, "nodeid": nodeid, "outcome": "failed", "duration_ms": 1000},
    ]
    (events / "order-lifecycle__default-default.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )

    out = tmp_path / "site"
    result = _run("--project-root", project, "-o", out, "--run-id", "20260806T101500Z-a1b2c3")
    assert result.exit_code == 0, result.output
    assert "FAILED" in result.output
    assert json.loads((out / "report.json").read_text(encoding="utf-8"))["totals"]["exit_code"] == 1


# ── placement ───────────────────────────────────────────────────────────────


def test_the_default_site_is_not_archived_by_a_later_bundle(project: Path):
    """Without the skip, the site's copied screenshots would be archived a second
    time and roughly double the bundle."""
    assert _run("--project-root", project).exit_code == 0
    assert (_site(project) / "index.html").is_file()

    manifest = create_bundle(project_root=project, fmt="gz").manifest
    assert not [f for f in manifest.files if f.path.startswith("output/report/")]


def test_a_site_elsewhere_under_output_warns_that_it_will_be_archived(project: Path):
    result = _run("--project-root", project, "-o", project / "output" / "site")
    assert result.exit_code == 0, result.output
    assert "will archive it" in result.output


def test_a_site_outside_output_says_nothing(project: Path, tmp_path: Path):
    result = _run("--project-root", project, "-o", tmp_path / "public")
    assert result.exit_code == 0, result.output
    assert "will archive it" not in result.output
