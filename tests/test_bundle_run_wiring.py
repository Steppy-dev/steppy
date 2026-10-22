"""``steppy run --bundle`` — the plumbing that captures a bundle's inputs.

Every assertion here has a mirror in ``tests/test_run_byte_identical.py``: the
point of these three env vars and one argv flag is that they appear *only* when
asked for, so the default path stays exactly what it was.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from steppy.runmeta import bundle_dirs, new_run_id, read_run_sidecar, write_run_sidecar


@pytest.fixture
def fake_tests_dir(tmp_path: Path) -> Path:
    tests_dir = tmp_path / "tests" / "e2e"
    tests_dir.mkdir(parents=True)
    (tests_dir / "conftest.py").write_text("# conftest", encoding="utf-8")
    (tests_dir / "test_example.py").write_text("# test", encoding="utf-8")
    return tests_dir


def _invoke(fake_tests_dir: Path, args: list[str], cwd: Path) -> tuple[list[str], dict]:
    captured: dict = {}

    def fake_run(cmd, env=None, cwd=None, **kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env or os.environ)
        result = MagicMock()
        result.returncode = 0
        return result

    from steppy.cli import app

    runner = CliRunner()
    previous = Path.cwd()
    os.chdir(cwd)
    try:
        with patch("steppy.cli.subprocess.run", side_effect=fake_run):
            runner.invoke(app, ["run", "--tests-dir", str(fake_tests_dir), *args])
    finally:
        os.chdir(previous)
    return captured.get("cmd", []), captured.get("env", {})


def test_bundle_flag_sets_exactly_the_env_it_needs(fake_tests_dir: Path, tmp_path: Path):
    _cmd, env = _invoke(fake_tests_dir, ["--bundle"], tmp_path)
    assert env["STEPPY_RUN_ID"]
    assert env["STEPPY_EVENTS_DIR"].endswith(str(Path("output") / "events" / env["STEPPY_RUN_ID"]))
    assert env["STEPPY_STEP_EVENTS"] == "1"
    steppy_keys = {k for k in env if k.startswith("STEPPY_")}
    assert steppy_keys == {
        "STEPPY_RUN_ID",
        "STEPPY_EVENTS_DIR",
        "STEPPY_STEP_EVENTS",
    }, f"--bundle set unexpected env vars: {sorted(steppy_keys)}"


def test_step_events_can_be_opted_out_via_the_environment(fake_tests_dir: Path, tmp_path: Path, monkeypatch):
    """Two extra file appends per step are cheap against a browser action, but
    a very long suite may still not want them. The env var is the escape hatch
    rather than a second CLI flag."""
    monkeypatch.setenv("STEPPY_STEP_EVENTS", "0")
    _cmd, env = _invoke(fake_tests_dir, ["--bundle"], tmp_path)
    assert env.get("STEPPY_STEP_EVENTS") == "0"
    # The rest of the bundle capture is unaffected.
    assert env["STEPPY_EVENTS_DIR"]


def test_bundle_flag_adds_junitxml_at_a_fixed_path(fake_tests_dir: Path, tmp_path: Path):
    cmd, env = _invoke(fake_tests_dir, ["--bundle"], tmp_path)
    junit_args = [a for a in cmd if a.startswith("--junitxml=")]
    assert len(junit_args) == 1, cmd
    expected = tmp_path / "output" / "junit" / env["STEPPY_RUN_ID"] / "run.xml"
    assert Path(junit_args[0].split("=", 1)[1]) == expected


def test_without_bundle_nothing_changes(fake_tests_dir: Path, tmp_path: Path):
    cmd, env = _invoke(fake_tests_dir, [], tmp_path)
    assert not [k for k in env if k.startswith("STEPPY_")]
    assert not [a for a in cmd if a.startswith("--junitxml=")]
    assert not (tmp_path / "output" / ".steppy-run.json").exists()


def test_bundle_writes_the_run_sidecar(fake_tests_dir: Path, tmp_path: Path):
    # --scenario keeps selection off the scenarios/ directory (there isn't one
    # here) while --guide still applies the guide *mode*, which is the
    # composition rule cli.run documents.
    _cmd, env = _invoke(
        fake_tests_dir, ["--bundle", "--guide", "--scenario", "example", "--locales", "fr,nl"], tmp_path
    )
    sidecar = read_run_sidecar(tmp_path)
    assert sidecar is not None
    assert sidecar["run_id"] == env["STEPPY_RUN_ID"]
    assert sidecar["spec"]["guide"] is True
    assert sidecar["spec"]["locales"] == ["fr", "nl"]
    # --guide implies both of these; the sidecar records what actually happened,
    # not what was typed.
    assert sidecar["spec"]["no_asserts"] is True
    assert sidecar["spec"]["screenshots"] is True


def test_bundle_and_coverage_share_one_run_id(fake_tests_dir: Path, tmp_path: Path):
    """Otherwise `bundle create` would archive coverage records filed under a
    different id than the bundle claims."""
    _cmd, env = _invoke(fake_tests_dir, ["--bundle", "--coverage"], tmp_path)
    assert env["STEPPY_COVERAGE"] == "1"
    assert env["STEPPY_EVENTS_DIR"].endswith(env["STEPPY_RUN_ID"])
    sidecar = read_run_sidecar(tmp_path)
    assert sidecar["run_id"] == env["STEPPY_RUN_ID"]


def test_configured_junit_xml_is_not_duplicated(fake_tests_dir: Path, tmp_path: Path):
    """pytest honours only the LAST --junitxml, so appending a second would
    silently discard the user's configured report."""
    (tmp_path / "config.yaml").write_text(
        "apps:\n  editor:\n    name: N\n    url: http://x\n    auth:\n      mode: form\n"
        "      credentials: {username: u, password: p}\n"
        "report:\n  junit_xml: output/reports/mine.xml\n",
        encoding="utf-8",
    )
    cmd, _env = _invoke(fake_tests_dir, ["--bundle"], tmp_path)
    junit_args = [a for a in cmd if a.startswith("--junitxml=")]
    assert len(junit_args) == 1
    assert junit_args[0].endswith("mine.xml")


# ── worker side ─────────────────────────────────────────────────────────────


def test_worker_junit_is_per_scenario(tmp_path: Path):
    """N workers share one output/ mount, so one fixed path would leave only
    the last finisher's report."""
    from steppy.worker import _build_pytest_argv

    junit_dir = tmp_path / "junit"
    argv = _build_pytest_argv(
        Path("tests/e2e/test_a.py"), True, {"STEPPY_JUNIT_DIR": str(junit_dir)}, slug="order-lifecycle"
    )
    assert f"--junitxml={junit_dir / 'order-lifecycle.xml'}" in argv


def test_worker_argv_unchanged_without_the_env_var():
    from steppy.worker import _build_pytest_argv

    plain = _build_pytest_argv(Path("tests/e2e/test_a.py"), False, {})
    with_slug = _build_pytest_argv(Path("tests/e2e/test_a.py"), False, {}, slug="order-lifecycle")
    assert plain == with_slug
    assert not [a for a in plain if a.startswith("--junitxml")]


# ── compose override ────────────────────────────────────────────────────────


def _render(**kwargs) -> str:
    from steppy._parallel.compose import ComposeIntrospection, render_override

    return render_override(
        introspection=ComposeIntrospection(services_with_ports=[], networks=[]),
        worker_image="steppy-worker",
        image_tag="0.3.0-abc",
        worker_index=1,
        run_id="RUN1",
        project_root=Path("/proj"),
        tmp_dir=Path("/tmp/x"),
        base_url_override="{}",
        locales="",
        layouts="",
        guide_mode="",
        no_asserts="",
        screenshots="",
        **kwargs,
    )


def test_override_is_byte_identical_when_bundle_is_off():
    assert _render() == _render(events_dir="", junit_dir="")


def test_override_emits_both_dirs_when_bundle_is_on():
    yaml = _render(events_dir="/steppy/project/output/events/RUN1", junit_dir="/steppy/project/output/junit/RUN1")
    assert "STEPPY_EVENTS_DIR: '/steppy/project/output/events/RUN1'" in yaml
    assert "STEPPY_JUNIT_DIR: '/steppy/project/output/junit/RUN1'" in yaml
    assert "STEPPY_STEP_EVENTS: '1'" in yaml


def test_override_honours_the_step_events_opt_out(monkeypatch):
    monkeypatch.setenv("STEPPY_STEP_EVENTS", "0")
    yaml = _render(events_dir="/steppy/project/output/events/RUN1")
    assert "STEPPY_EVENTS_DIR" in yaml
    assert "STEPPY_STEP_EVENTS" not in yaml


# ── runmeta ─────────────────────────────────────────────────────────────────


def test_run_ids_sort_chronologically():
    """``coverage.merge.latest_run_id`` and the bundler's discovery rung both
    resolve "newest" by taking the lexicographically last name."""
    ids = sorted(new_run_id() for _ in range(5))
    assert ids == sorted(ids)
    assert all(i[:8].isdigit() and i[8] == "T" for i in ids)


def test_sidecar_round_trips(tmp_path: Path):
    written = write_run_sidecar(tmp_path, run_id="R", spec={"workers": 3}, slugs=["a", "b"])
    assert written is not None
    loaded = read_run_sidecar(tmp_path)
    assert loaded["run_id"] == "R" and loaded["slugs"] == ["a", "b"]
    assert json.loads(written.read_text(encoding="utf-8"))["spec"]["workers"] == 3


def test_sidecar_write_failure_is_not_fatal(tmp_path: Path):
    blocker = tmp_path / "output"
    blocker.write_text("not a directory", encoding="utf-8")
    assert write_run_sidecar(tmp_path, run_id="R", spec={}) is None
    assert read_run_sidecar(tmp_path) is None


def test_bundle_dirs_are_per_run(tmp_path: Path):
    events, junit = bundle_dirs(tmp_path, "RUN1")
    assert events == tmp_path / "output" / "events" / "RUN1"
    assert junit == tmp_path / "output" / "junit" / "RUN1"
