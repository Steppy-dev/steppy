"""CLI wiring for coverage: --coverage env assembly, post-run merge hook,
and the `steppy coverage` command group (merge / refresh / report / triage).

`steppy run` env capture mirrors test_run_byte_identical.py (subprocess.run
patched); the command-group tests drive a real tmp project end-to-end via
monkeypatch.chdir — no browser, no network.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

runner = CliRunner()

_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")


@pytest.fixture
def fake_tests_dir(tmp_path: Path) -> Path:
    tests_dir = tmp_path / "tests" / "e2e"
    tests_dir.mkdir(parents=True)
    (tests_dir / "conftest.py").write_text("# conftest", encoding="utf-8")
    (tests_dir / "test_example.py").write_text("# test", encoding="utf-8")
    return tests_dir


def _capture_run_env(fake_tests_dir: Path, extra_args: list[str]) -> tuple[dict, object]:
    captured = {}

    def fake_run(cmd, env=None, cwd=None, **kwargs):
        captured["env"] = dict(env or os.environ)
        result = MagicMock()
        result.returncode = 0
        return result

    from steppy.cli import app

    with patch("steppy.cli.subprocess.run", side_effect=fake_run):
        result = runner.invoke(app, ["run", "--tests-dir", str(fake_tests_dir), *extra_args])
    return captured.get("env", {}), result


def test_run_without_coverage_sets_no_coverage_env(fake_tests_dir):
    env, result = _capture_run_env(fake_tests_dir, [])
    assert result.exit_code == 0, result.output
    assert "STEPPY_COVERAGE" not in env
    assert "STEPPY_RUN_ID" not in env


def test_run_coverage_sets_env_and_run_id(fake_tests_dir):
    env, result = _capture_run_env(fake_tests_dir, ["--coverage"])
    assert env.get("STEPPY_COVERAGE") == "1"
    assert _RUN_ID_RE.match(env.get("STEPPY_RUN_ID", "")), env.get("STEPPY_RUN_ID")
    # No records exist -> merge warns but pytest's exit code (0) is preserved.
    assert result.exit_code == 0, result.output
    assert "Coverage merge failed" in result.output


def test_run_coverage_invokes_post_run_merge(fake_tests_dir):
    def fake_run(cmd, env=None, cwd=None, **kwargs):
        fake_run.env = dict(env)
        result = MagicMock()
        result.returncode = 0
        return result

    merge_mock = MagicMock(
        return_value={"run_id": "x", "totals": {"scenarios": 1, "nodes": 7}, "warnings": [], "scenarios": {}}
    )
    from steppy.cli import app

    with (
        patch("steppy.cli.subprocess.run", side_effect=fake_run),
        patch("steppy.coverage.merge.merge_run", merge_mock),
    ):
        result = runner.invoke(app, ["run", "--tests-dir", str(fake_tests_dir), "--coverage"])

    assert result.exit_code == 0, result.output
    assert merge_mock.call_count == 1
    passed_run_id = merge_mock.call_args[0][1]
    assert passed_run_id == fake_run.env["STEPPY_RUN_ID"]
    assert "Coverage: merged 1 scenario(s), 7 map node(s)" in result.output


def test_run_coverage_merges_even_on_pytest_failure(fake_tests_dir):
    def fake_run(cmd, env=None, cwd=None, **kwargs):
        result = MagicMock()
        result.returncode = 2  # pytest failures
        return result

    merge_mock = MagicMock(
        return_value={"run_id": "x", "totals": {"scenarios": 1, "nodes": 1}, "warnings": [], "scenarios": {}}
    )
    from steppy.cli import app

    with (
        patch("steppy.cli.subprocess.run", side_effect=fake_run),
        patch("steppy.coverage.merge.merge_run", merge_mock),
    ):
        result = runner.invoke(app, ["run", "--tests-dir", str(fake_tests_dir), "--coverage"])

    assert merge_mock.call_count == 1
    assert result.exit_code == 2  # pytest's exit code preserved


# ── steppy coverage command group, end-to-end in a tmp project ──────────────

_CONFIG_YAML = dedent("""\
    apps:
      editor:
        name: "Demo"
        url: "http://localhost:4200"
        auth:
          mode: bypass

    coverage:
      apis:
        - name: demo-api
          openapi: "openapi.json"
          base_urls: ["http://localhost:8080"]
    """)

_OPENAPI = {
    "openapi": "3.1.0",
    "paths": {
        "/orders": {"get": {"operationId": "listOrders"}},
        "/webhooks/stripe": {"post": {"operationId": "stripeCallback"}},
    },
}

_RECORD = {
    "steppy_coverage_record": 1,
    "run_id": "20260611T120000Z-cli001",
    "scenario": "demo",
    "role": "editor",
    "noauth": False,
    "locale": None,
    "layout": None,
    "asserts_enabled": True,
    "guide_mode": False,
    "outcome": "passed",
    "started_at": "2026-06-11T12:00:00Z",
    "finished_at": "2026-06-11T12:00:10Z",
    "nav_events": [{"u": "http://localhost:4200/orders", "k": "nav", "t": 1.0}],
    "spa_events": [],
    "requests": [{"m": "GET", "u": "http://localhost:8080/orders", "rt": "xhr"}],
    "requests_dropped": 0,
    "js_coverage": {"ok": False, "reason": "test fixture", "scripts": []},
}


@pytest.fixture
def coverage_project(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "config.yaml").write_text(_CONFIG_YAML, encoding="utf-8")
    (tmp_path / "openapi.json").write_text(json.dumps(_OPENAPI), encoding="utf-8")
    run_dir = tmp_path / "output" / "coverage" / "raw" / _RECORD["run_id"] / "demo"
    run_dir.mkdir(parents=True)
    (run_dir / "editor_page__default-default.json").write_text(json.dumps(_RECORD), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_coverage_group_end_to_end(coverage_project):
    from steppy.cli import app

    merge = runner.invoke(app, ["coverage", "merge", "--run-id", _RECORD["run_id"]])
    assert merge.exit_code == 0, merge.output
    assert "1 scenario(s)" in merge.output
    assert (coverage_project / ".steppy" / "coverage.db").exists()

    refresh = runner.invoke(app, ["coverage", "refresh"])
    assert refresh.exit_code == 0, refresh.output
    assert "openapi:demo-api: 2 declared endpoint(s)" in refresh.output

    triage = runner.invoke(
        app,
        [
            "coverage",
            "triage",
            "set",
            "/webhooks/stripe",
            "--method",
            "post",
            "--api",
            "demo-api",
            "--flag",
            "non_ui",
            "--note",
            "payment callback",
        ],
    )
    assert triage.exit_code == 0, triage.output

    listing = runner.invoke(app, ["coverage", "triage", "list", "--flag", "non_ui"])
    assert listing.exit_code == 0
    assert "/webhooks/stripe" in listing.output

    report = runner.invoke(app, ["coverage", "report", "--json", "gaps.json"])
    assert report.exit_code == 0, report.output
    assert "declared-unreached" in report.output

    gaps = json.loads((coverage_project / "gaps.json").read_text(encoding="utf-8"))
    tested = {(e["method"], e["key"]) for e in gaps["endpoints"]["tested"]}
    assert tested == {("GET", "/orders")}
    unreached = gaps["endpoints"]["declared_unreached"]
    assert [(e["method"], e["key"], e["triage"]) for e in unreached] == [("POST", "/webhooks/stripe", "non_ui")]
    assert gaps["counts"]["endpoints"]["declared_unreached_actionable"] == 0


def test_coverage_merge_without_records_errors(coverage_project):
    from steppy.cli import app

    result = runner.invoke(app, ["coverage", "merge", "--run-id", "20990101T000000Z-none00"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_coverage_report_without_store_errors(coverage_project):
    from steppy.cli import app

    result = runner.invoke(app, ["coverage", "report"])
    assert result.exit_code == 1
    assert "No coverage store" in result.output


def test_coverage_refresh_unknown_api_errors(coverage_project):
    from steppy.cli import app

    result = runner.invoke(app, ["coverage", "refresh", "--api", "nope"])
    assert result.exit_code == 1
    assert "Unknown API" in result.output


def test_coverage_triage_invalid_flag_errors(coverage_project):
    from steppy.cli import app

    result = runner.invoke(app, ["coverage", "triage", "set", "/x", "--flag", "maybe"])
    assert result.exit_code == 1
    assert "invalid triage flag" in result.output
