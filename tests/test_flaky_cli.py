"""``steppy flaky`` — the CLI over the run history.

Covers the output contract (table / JSON / empty), the filters, and the two
structural guarantees the command inherits from being a leaf module: it must
not drag parallel-mode code into ``import steppy.cli``, and it must be usable
in a project that has never run in parallel.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from steppy._parallel.queue import ScenarioResult
from steppy._parallel.retry import PassResult, classify
from steppy.cli import app
from steppy.history import append_run


runner = CliRunner()


def _result(slug, exit_code=0, duration=1.0):
    return ScenarioResult(
        slug=slug,
        worker_id="w1",
        exit_code=exit_code,
        duration_s=duration,
        started_at=0.0,
        finished_at=duration,
    )


def _record(root: Path, run_id: str, main: list, retry: list | None = None):
    passes = [PassResult(attempt=1, coord_root=root, results=main)]
    if retry is not None:
        passes.append(PassResult(attempt=2, coord_root=root / "retry-1", results=retry))
    append_run(
        project_root=root,
        run_id=run_id,
        workers=4,
        passes=passes,
        verdict=classify(passes) if retry is not None else None,
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_reports_nothing_when_no_history(project):
    result = runner.invoke(app, ["flaky"])
    assert result.exit_code == 0
    assert "No run history yet" in result.output
    assert "--workers" in result.output, "should say how to produce history"


def test_ranks_the_worst_offender_first(project):
    for i in range(10):
        wobbly_failed = i < 7
        _record(
            project,
            f"r{i}",
            [_result("stable"), _result("wobbly", 1 if wobbly_failed else 0)],
            retry=[_result("wobbly", 0)] if wobbly_failed else [],
        )
    result = runner.invoke(app, ["flaky"])
    assert result.exit_code == 0
    assert "wobbly" in result.output
    assert "70%" in result.output
    # Worst first: wobbly's row precedes stable's.
    assert result.output.index("wobbly") < result.output.index("stable")


def test_json_output_is_machine_readable(project):
    _record(project, "r1", [_result("a", 1)], retry=[_result("a", 0)])
    result = runner.invoke(app, ["flaky", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["runs"] == 1
    assert payload["scenarios"][0]["slug"] == "a"
    assert payload["scenarios"][0]["flakes"] == 1


def test_json_output_when_empty(project):
    result = runner.invoke(app, ["flaky", "--json"])
    assert json.loads(result.output) == {"runs": 0, "scenarios": []}


def test_min_rate_filters_the_healthy_out(project):
    for i in range(10):
        _record(project, f"r{i}", [_result("stable"), _result("wobbly", 1 if i < 7 else 0)])
    result = runner.invoke(app, ["flaky", "--min-rate", "0.5"])
    assert "wobbly" in result.output
    assert "stable" not in result.output


def test_min_rate_above_everything_says_so(project):
    _record(project, "r1", [_result("a")])
    result = runner.invoke(app, ["flaky", "--min-rate", "0.9"])
    assert result.exit_code == 0
    assert "No scenario exceeded" in result.output


def test_last_narrows_the_window(project):
    """Old failures shouldn't haunt a scenario that has since been fixed."""
    for i in range(5):
        _record(project, f"old{i}", [_result("fixed-since", 1)])
    for i in range(5):
        _record(project, f"new{i}", [_result("fixed-since", 0)])
    result = runner.invoke(app, ["flaky", "--last", "5", "--json"])
    payload = json.loads(result.output)
    assert payload["runs"] == 5
    assert payload["scenarios"][0]["flake_rate"] == 0.0


def test_limit_caps_the_table(project):
    _record(project, "r1", [_result(f"s{i}", 1) for i in range(10)])
    result = runner.invoke(app, ["flaky", "--limit", "3"])
    assert "and 7 more" in result.output


def test_prune_trims_the_file(project):
    for i in range(6):
        _record(project, f"r{i}", [_result("a")])
    result = runner.invoke(app, ["flaky", "--prune", "2"])
    assert result.exit_code == 0
    assert "Pruned 4 record(s)" in result.output


def test_flaky_does_not_import_parallel_modules(project):
    """Same guard as tests/test_no_parallel_import_on_default_path.py — the
    command reads a JSONL file and must not pull in coordinator/_parallel."""
    prefixes = ("steppy.coordinator", "steppy.worker", "steppy._parallel")
    for name in list(sys.modules):
        if any(name == p or name.startswith(p + ".") for p in prefixes):
            del sys.modules[name]

    from steppy.cli import app as fresh_app

    runner.invoke(fresh_app, ["flaky"])
    leaked = sorted(n for n in sys.modules if any(n == p or n.startswith(p + ".") for p in prefixes))
    assert not leaked, f"`steppy flaky` pulled in parallel modules: {leaked}"
